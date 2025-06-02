from django_plotly_dash import DjangoDash

from dash import html, dcc, Output, Input, State, no_update, dash_table
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import matplotlib.pyplot as plt # analyze_environment_shape içinde renk haritası için kullanılıyor

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
SENSOR_SCRIPT_FILENAME = 'sensor_script.py' # Bu, Dash paneli tarafından çağrılan betiğin adı
SENSOR_SCRIPT_PATH = os.path.join(PROJECT_ROOT_DIR, SENSOR_SCRIPT_FILENAME)
LOCK_FILE_PATH_FOR_DASH = '/tmp/sensor_scan_script.lock' # Bu Dash paneli, sensör betiğinin lock dosyasını kontrol etmez
PID_FILE_PATH_FOR_DASH = '/tmp/sensor_scan_script.pid'   # Bu Dash paneli, sensör betiğinin PID dosyasını doğrudan yönetmez

DEFAULT_UI_SCAN_START_ANGLE = 0
DEFAULT_UI_SCAN_END_ANGLE = 180
DEFAULT_UI_SCAN_STEP_ANGLE = 10
DEFAULT_UI_BUZZER_DISTANCE = 10

# --- DASH UYGULAMASININ TANIMLANMASI ---
# Bu satır, Django projenizin Dash uygulamasını bulabilmesi için gereklidir.
# 'RealtimeSensorDashboard' adı, Django admin panelinde veya URL'lerde kullanılacak addır.
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
            dbc.Col(html.Button('2D Taramayı Başlat', id='start-scan-button', n_clicks=0, className="btn btn-success btn-lg w-100 mb-2"), width=6),
            dbc.Col(html.Button('Taramayı Durdur', id='stop-scan-button', n_clicks=0, className="btn btn-danger btn-lg w-100 mb-2"), width=6)
        ]),
        html.Div(id='scan-status-message', style={'marginTop': '10px', 'minHeight': '40px', 'textAlign': 'center'}, className="mb-3"),
        html.Hr(),
        html.H6("Tarama Parametreleri:", className="mt-2"),
        dbc.InputGroup([dbc.InputGroupText("Başl. Açı (°)", style={"width": "120px"}), dbc.Input(id="start-angle-input", type="number", value=DEFAULT_UI_SCAN_START_ANGLE, min=0, max=359, step=5)], className="mb-2"), # Max açı 359'a çıkarıldı (step motor için)
        dbc.InputGroup([dbc.InputGroupText("Bitiş Açısı (°)", style={"width": "120px"}), dbc.Input(id="end-angle-input", type="number", value=DEFAULT_UI_SCAN_END_ANGLE, min=0, max=359, step=5)], className="mb-2"), # Max açı 359'a çıkarıldı
        dbc.InputGroup([dbc.InputGroupText("Adım Açısı (°)", style={"width": "120px"}), dbc.Input(id="step-angle-input", type="number", value=DEFAULT_UI_SCAN_STEP_ANGLE, min=0.1, max=45, step=0.1)], className="mb-2"), # Adım açısı float olabilir
        dbc.InputGroup([dbc.InputGroupText("Buzzer Mes. (cm)", style={"width": "120px"}), dbc.Input(id="buzzer-distance-input", type="number", value=DEFAULT_UI_BUZZER_DISTANCE, min=0, max=200, step=1)], className="mb-2"),
    ])
])

stats_panel = dbc.Card([
    dbc.CardHeader("Anlık Sensör Değerleri", className="bg-info text-white"),
    dbc.CardBody(
        dbc.Row([
            dbc.Col(html.Div([html.H6("Mevcut Açı:"), html.H4(id='current-angle', children="--°")]), width=3, className="text-center border-end"),
            dbc.Col(html.Div([html.H6("Mevcut Mesafe:"), html.H4(id='current-distance', children="-- cm")]), id='current-distance-col', width=3, className="text-center rounded border-end"),
            dbc.Col(html.Div([html.H6("Anlık Hız:"), html.H4(id='current-speed', children="-- cm/s")]), width=3, className="text-center border-end"),
            dbc.Col(html.Div([html.H6("Max. Algılanan Mesafe:"), html.H4(id='max-detected-distance', children="-- cm")]), width=3, className="text-center")
        ]))
], className="mb-3")

system_card = dbc.Card([
    dbc.CardHeader("Sistem Durumu", className="bg-secondary text-white"),
    dbc.CardBody([
        dbc.Row([dbc.Col(html.Div([html.H6("Sensör Betiği Durumu:"), html.H5(id='script-status', children="Beklemede")]))], className="mb-2"), # Başlık güncellendi
        dbc.Row([
            dbc.Col(html.Div([html.H6("Pi CPU Kullanımı:"), dbc.Progress(id='cpu-usage', value=0, color="success", style={"height": "20px"}, className="mb-1", label="0%")])),
            dbc.Col(html.Div([html.H6("Pi RAM Kullanımı:"), dbc.Progress(id='ram-usage', value=0, color="info", style={"height": "20px"}, className="mb-1", label="0%")]))
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
])

visualization_tabs = dbc.Tabs(
    [
        dbc.Tab(dcc.Graph(id='scan-map-graph', style={'height': '75vh'}), label="2D Kartezyen Harita", tab_id="tab-map"),
        dbc.Tab(dcc.Graph(id='polar-regression-graph', style={'height': '75vh'}), label="Regresyon Analizi", tab_id="tab-regression"),
        dbc.Tab(dcc.Graph(id='polar-graph', style={'height': '75vh'}), label="Polar Grafik", tab_id="tab-polar"),
        dbc.Tab(dcc.Graph(id='time-series-graph', style={'height': '75vh'}), label="Zaman Serisi (Mesafe)", tab_id="tab-time"),
        dbc.Tab(dcc.Loading(id="loading-datatable", children=[html.Div(id='tab-content-datatable')]), label="Veri Tablosu", tab_id="tab-datatable")
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
    dcc.Store(id='clustered-data-store'),
    dbc.Modal([
        dbc.ModalHeader(dbc.ModalTitle(id="modal-title")),
        dbc.ModalBody(id="modal-body"),
    ], id="cluster-info-modal", is_open=False, centered=True),
    dcc.Interval(id='interval-component-main', interval=3000, n_intervals=0),
    dcc.Interval(id='interval-component-system', interval=3000, n_intervals=0), # Sensör betiği durumunu kontrol için
])


# ==============================================================================
# --- YARDIMCI FONKSİYONLAR ---
# (Bu fonksiyonlar Dash paneli için gereklidir ve sensör betiğindekilerle aynı olabilir veya farklılık gösterebilir)
# ==============================================================================
def is_process_running(pid):
    """Verilen PID'nin çalışıp çalışmadığını kontrol eder (Dash paneli için)."""
    if pid is None: return False
    try:
        # os.kill(pid, 0) platform bağımlı olabilir ve Dash uygulamasının çalıştığı ortamda
        # her zaman doğru çalışmayabilir. psutil daha güvenilir bir alternatif olabilir.
        # Ancak basitlik adına şimdilik bu şekilde bırakılabilir veya psutil.pid_exists(pid) kullanılabilir.
        return psutil.pid_exists(pid) # psutil daha platform bağımsız
    except Exception: # psutil.NoSuchProcess vb.
        return False

def get_db_connection():
    """Veritabanı bağlantısı döndürür."""
    try:
        if not os.path.exists(DB_PATH):
            # print(f"DB dosyası ({DB_PATH}) bulunamadı.") # UI'da hata göstermek daha iyi
            return None, f"Veritabanı dosyası ({DB_PATH}) bulunamadı."
        conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, timeout=5) # Salt okunur mod
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
            # print(f"DB Hatası (get_latest_scan_id): {error}")
            return None
        internal_conn = True
    if conn_to_use:
        try:
            # Önce 'running' durumundaki en son tarama ID'sini al
            df_scan_running = pd.read_sql_query("SELECT id FROM servo_scans WHERE status = 'running' ORDER BY start_time DESC LIMIT 1", conn_to_use)
            if not df_scan_running.empty:
                latest_id = int(df_scan_running['id'].iloc[0])
            else:
                # 'running' yoksa, herhangi bir durumdaki en son tarama ID'sini al
                df_scan_last = pd.read_sql_query("SELECT id FROM servo_scans ORDER BY start_time DESC LIMIT 1", conn_to_use)
                if not df_scan_last.empty:
                    latest_id = int(df_scan_last['id'].iloc[0])
        except Exception as e:
            print(f"Son tarama ID alınırken hata: {e}") # Konsola logla
            latest_id = None
        finally:
            if internal_conn and conn_to_use: conn_to_use.close()
    return latest_id

# --- GRAFİK YARDIMCI FONKSİYONLARI ---
def add_scan_rays(fig, df):
    x_lines, y_lines = [], []
    for _, row in df.iterrows():
        x_lines.extend([0, row['y_cm'], None]); y_lines.extend([0, row['x_cm'], None])
    fig.add_trace(go.Scatter(x=x_lines, y=y_lines, mode='lines', line=dict(color='rgba(255,100,100,0.4)', dash='dash', width=1), showlegend=False))

def add_sector_area(fig, df):
    poly_x, poly_y = df['y_cm'].tolist(), df['x_cm'].tolist()
    fig.add_trace(go.Scatter(x=[0]+poly_x, y=[0]+poly_y, mode='lines', fill='toself', fillcolor='rgba(255,0,0,0.15)', line=dict(color='rgba(255,0,0,0.4)'), name='Taranan Sektör'))

def add_sensor_position(fig):
    fig.add_trace(go.Scatter(x=[0], y=[0], mode='markers', marker=dict(size=12, symbol='circle', color='red'), name='Sensör'))

def update_polar_graph(fig, df):
    fig.add_trace(go.Scatterpolar(r=df['mesafe_cm'], theta=df['derece'], mode='lines+markers', name='Mesafe'))
    fig.update_layout(polar=dict(radialaxis=dict(visible=True, range=[0,250]), angularaxis=dict(direction="clockwise", period=360))) # Max menzil ve periyot güncellendi

def update_time_series_graph(fig, df):
    df_s = df.sort_values(by='timestamp')
    fig.add_trace(go.Scatter(x=pd.to_datetime(df_s['timestamp'], unit='s'), y=df_s['mesafe_cm'], mode='lines+markers', name='Mesafe'))
    fig.update_layout(xaxis_title="Zaman", yaxis_title="Mesafe (cm)")

# --- ANALİZ YARDIMCI FONKSİYONLARI ---
def find_clearest_path(df_valid):
    if df_valid.empty: return "En açık yol için veri yok."
    try:
        # Sadece geçerli (250cm altı) ve pozitif mesafeler arasından en uzağı bul
        df_filtered = df_valid[df_valid['mesafe_cm'] > 0]
        if df_filtered.empty: return "Geçerli pozitif mesafe bulunamadı."
        cp = df_filtered.loc[df_filtered['mesafe_cm'].idxmax()]
        return f"En Açık Yol: {cp['derece']:.1f}° yönünde, {cp['mesafe_cm']:.1f} cm."
    except Exception as e: return f"En açık yol hesaplanamadı: {e}"

def analyze_polar_regression(df_valid):
    if len(df_valid) < 5: return None, "Polar regresyon için yetersiz veri."
    X, y = df_valid[['derece']].values, df_valid['mesafe_cm'].values
    try:
        ransac = RANSACRegressor(random_state=42); ransac.fit(X,y)
        slope = ransac.estimator_.coef_[0]
        inf = f"Yüzey dairesel/paralel (Eğim:{slope:.3f})" if abs(slope)<0.1 else (f"Yüzey açı arttıkça uzaklaşıyor (Eğim:{slope:.3f})" if slope>0 else f"Yüzey açı arttıkça yaklaşıyor (Eğim:{slope:.3f})")
        xr = np.array([df_valid['derece'].min(), df_valid['derece'].max()]).reshape(-1,1)
        return {'x': xr.flatten(), 'y': ransac.predict(xr)}, "Polar Regresyon: " + inf + " cm/derece." # Birim eklendi
    except Exception as e: return None, f"Polar regresyon hatası: {e}"

def analyze_environment_shape(fig, df_valid):
    points_all = df_valid[['y_cm', 'x_cm']].to_numpy()
    if len(points_all) < 10:
        df_valid['cluster'] = -2; return "Analiz için yetersiz veri.", df_valid
    
    db = DBSCAN(eps=15, min_samples=3).fit(points_all) # eps ve min_samples ayarlanabilir
    labels = db.labels_; df_valid['cluster'] = labels
    
    desc = []
    unique_clusters = set(labels)
    num_actual_clusters = len(unique_clusters - {-1}) # Gürültü olmayan küme sayısı
    
    if num_actual_clusters > 0:
        desc.append(f"{num_actual_clusters} potansiyel nesne kümesi bulundu.")
    else:
        desc.append("Belirgin bir nesne kümesi bulunamadı (DBSCAN).")

    # --- RANSAC ile Duvar/Koridor/Köşe Tespiti (Önceki versiyonlardaki mantık buraya entegre edilebilir) ---
    # Bu kısım, projenin karmaşıklığına ve isteğe bağlı olarak eklenebilir.
    # Şimdilik sadece küme bilgisini ve sayısını raporluyoruz.

    # Kümeleri grafiğe çiz
    colors = plt.cm.get_cmap('viridis', len(unique_clusters) if len(unique_clusters) > 0 else 1)
    for k_label in unique_clusters:
        cluster_points_to_plot = points_all[labels == k_label]
        if k_label == -1: # Gürültü noktaları
            color_val, point_size, name_val = 'rgba(128,128,128,0.3)', 5, 'Gürültü/Diğer'
        else: # Küme noktaları
            norm_k = k_label / (len(unique_clusters) - 1) if len(unique_clusters) > 1 else 0
            raw_col = colors(norm_k)
            color_val = f'rgba({raw_col[0]*255:.0f},{raw_col[1]*255:.0f},{raw_col[2]*255:.0f},0.9)'
            point_size, name_val = 8, f'Küme {k_label}'
        fig.add_trace(go.Scatter(
            x=cluster_points_to_plot[:,0], y=cluster_points_to_plot[:,1], mode='markers',
            marker=dict(color=color_val, size=point_size), name=name_val,
            customdata=[k_label] * len(cluster_points_to_plot) # Tıklama için küme etiketini sakla
        ))
    return " ".join(desc), df_valid


# ==============================================================================
# --- CALLBACK FONKSİYONLARI ---
# ==============================================================================

# --- KONTROL CALLBACK'LERİ ---
@app.callback(
    Output('scan-status-message', 'children'),
    [Input('start-scan-button', 'n_clicks')],
    [State('start-angle-input', 'value'), State('end-angle-input', 'value'),
     State('step-angle-input', 'value'), State('buzzer-distance-input', 'value')],
    prevent_initial_call=True)
def handle_start_scan_script(n_clicks_start, start_angle_val, end_angle_val, step_angle_val, buzzer_distance_val):
    if n_clicks_start == 0: return no_update
    
    start_a = start_angle_val if start_angle_val is not None else DEFAULT_UI_SCAN_START_ANGLE
    end_a = end_angle_val if end_angle_val is not None else DEFAULT_UI_SCAN_END_ANGLE
    step_a = step_angle_val if step_angle_val is not None else DEFAULT_UI_SCAN_STEP_ANGLE
    buzzer_d = buzzer_distance_val if buzzer_distance_val is not None else DEFAULT_UI_BUZZER_DISTANCE

    # Açı ve adım doğrulaması (step motor 0-360 derece çalışabilir)
    if not (0 <= start_a <= 359 and 0 <= end_a <= 359):
        return dbc.Alert("Başlangıç ve Bitiş açıları 0-359 arasında olmalıdır!", color="danger")
    if not (0.1 <= abs(step_a) <= 45): # Adım açısı float olabilir ve makul bir aralıkta olmalı
        return dbc.Alert("Adım açısı (mutlak değer) 0.1-45 arasında olmalıdır!", color="danger")
    if not (0 <= buzzer_d <= 200):
        return dbc.Alert("Buzzer mesafesi 0-200 cm arasında olmalıdır!", color="danger")

    # Sensör betiğinin çalışıp çalışmadığını kontrol et (PID dosyası üzerinden)
    current_pid = None
    if os.path.exists(PID_FILE_PATH_FOR_DASH): # Bu PID dosyası sensör betiğine ait olmalı
        try:
            with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                pid_str = pf.read().strip()
                if pid_str: current_pid = int(pid_str)
        except (IOError, ValueError):
            current_pid = None
    
    if current_pid and is_process_running(current_pid):
        return dbc.Alert(f"Sensör betiği zaten çalışıyor (PID: {current_pid}). Önce durdurun.", color="warning")

    # Eski kilit ve PID dosyalarını temizle (sensör betiğine ait olanları)
    for f_path in [LOCK_FILE_PATH_FOR_DASH, PID_FILE_PATH_FOR_DASH]: # Bu dosya adları sensör betiğindekiyle aynı olmalı
        if os.path.exists(f_path):
            try: os.remove(f_path)
            except OSError as e: return dbc.Alert(f"Kalıntı dosya ({f_path}) silinemedi: {e}. Elle silin.", color="danger")
    try:
        python_executable = sys.executable # Mevcut Python yorumlayıcısı
        if not os.path.exists(SENSOR_SCRIPT_PATH):
            return dbc.Alert(f"Sensör betiği bulunamadı: {SENSOR_SCRIPT_PATH}", color="danger")

        cmd = [
            python_executable, SENSOR_SCRIPT_PATH,
            "--start_angle", str(start_a), "--end_angle", str(end_a),
            "--step_angle", str(step_a), "--buzzer_distance", str(buzzer_d)
        ]
        log_file_path = os.path.join(PROJECT_ROOT_DIR, 'sensor_script.log') # Betik logları için

        # subprocess.Popen ile betiği arka planda başlat
        with open(log_file_path, 'w') as log_file: # Log dosyasını her başlatmada temizle
            if os.name == 'nt': # Windows için
                 process = subprocess.Popen(cmd, creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP, stdout=log_file, stderr=log_file)
            else: # Linux/macOS için
                 process = subprocess.Popen(cmd, start_new_session=True, stdout=log_file, stderr=log_file)
        
        time.sleep(2.5) # Betiğin PID dosyasını oluşturması için bekleme

        if os.path.exists(PID_FILE_PATH_FOR_DASH): # Sensör betiğinin PID dosyasını kontrol et
            with open(PID_FILE_PATH_FOR_DASH, 'r') as pf_new: new_pid_str = pf_new.read().strip()
            return dbc.Alert(f"Sensör betiği başlatıldı (PID: {new_pid_str})...", color="success")
        else:
            # PID dosyası oluşmadıysa logları kontrol et
            log_content = ""; # ... (log okuma ve hata gösterme mantığı eklenebilir)
            return dbc.Alert(f"Sensör betiği PID dosyası ({PID_FILE_PATH_FOR_DASH}) oluşturmadı. Logları kontrol edin.", color="danger")
    except Exception as e:
        return dbc.Alert(f"Sensör betiği başlatılırken hata: {str(e)}", color="danger")

@app.callback(
    Output('scan-status-message', 'children', allow_duplicate=True),
    [Input('stop-scan-button', 'n_clicks')],
    prevent_initial_call=True)
def handle_stop_scan_script(n_clicks_stop):
    if n_clicks_stop == 0: return no_update
    pid_to_kill = None
    # Sensör betiğine ait PID dosyasını oku
    if os.path.exists(PID_FILE_PATH_FOR_DASH):
        try:
            with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                pid_str = pf.read().strip()
                if pid_str: pid_to_kill = int(pid_str)
        except (IOError, ValueError): pid_to_kill = None

    if pid_to_kill and is_process_running(pid_to_kill):
        try:
            os.kill(pid_to_kill, signal.SIGTERM) # Önce nazikçe durdurmayı dene
            time.sleep(2.0)
            if is_process_running(pid_to_kill): # Hala çalışıyorsa
                os.kill(pid_to_kill, signal.SIGKILL) # Zorla durdur
                time.sleep(0.5)
            if not is_process_running(pid_to_kill):
                # PID ve Lock dosyalarını temizle (sensör betiğine ait olanları)
                for f_path in [PID_FILE_PATH_FOR_DASH, LOCK_FILE_PATH_FOR_DASH]:
                    if os.path.exists(f_path): os.remove(f_path)
                return dbc.Alert(f"Sensör betiği (PID: {pid_to_kill}) başarıyla durduruldu.", color="info")
            else:
                return dbc.Alert(f"Sensör betiği (PID: {pid_to_kill}) durdurulamadı!", color="danger")
        except ProcessLookupError: # Process zaten yoksa
             for f_path in [PID_FILE_PATH_FOR_DASH, LOCK_FILE_PATH_FOR_DASH]:
                 if os.path.exists(f_path): os.remove(f_path)
             return dbc.Alert(f"Sensör betiği (PID: {pid_to_kill}) zaten çalışmıyordu.", color="warning")
        except Exception as e:
            return dbc.Alert(f"Sensör betiği (PID: {pid_to_kill}) durdurulurken hata: {e}", color="danger")
    else:
        # Çalışan betik yoksa bile kalıntı dosyaları temizle
        for f_path in [PID_FILE_PATH_FOR_DASH, LOCK_FILE_PATH_FOR_DASH]:
            if os.path.exists(f_path): os.remove(f_path)
        return dbc.Alert("Çalışan bir sensör betiği bulunamadı.", color="warning")

# --- ANLIK VERİ VE SİSTEM DURUMU GÜNCELLEME CALLBACK'LERİ ---
@app.callback(
    [Output('current-angle', 'children'), Output('current-distance', 'children'),
     Output('current-speed', 'children'), Output('current-distance-col', 'style'),
     Output('max-detected-distance', 'children')],
    [Input('interval-component-main', 'n_intervals')]
)
def update_realtime_values(n_intervals):
    conn, error = get_db_connection()
    angle_s, dist_s, speed_s, max_dist_s = "--°", "-- cm", "-- cm/s", "-- cm"
    dist_style = {'padding':'10px', 'transition':'background-color 0.5s ease', 'borderRadius':'5px'}
    if error: return angle_s, dist_s, speed_s, dist_style, max_dist_s
    if conn:
        try:
            latest_id = get_latest_scan_id_from_db(conn)
            if latest_id:
                q_curr = f"SELECT mesafe_cm, derece, hiz_cm_s FROM scan_points WHERE scan_id = {latest_id} ORDER BY id DESC LIMIT 1"
                df_p = pd.read_sql_query(q_curr, conn)
                q_set = f"SELECT buzzer_distance_setting FROM servo_scans WHERE id = {latest_id}"
                df_set = pd.read_sql_query(q_set, conn)
                buzzer_thr = float(df_set['buzzer_distance_setting'].iloc[0]) if not df_set.empty and pd.notnull(df_set['buzzer_distance_setting'].iloc[0]) else None
                
                if not df_p.empty:
                    d, a, s = df_p['mesafe_cm'].iloc[0], df_p['derece'].iloc[0], df_p['hiz_cm_s'].iloc[0]
                    angle_s = f"{a:.1f}°" if pd.notnull(a) else "--°" # Dereceyi 1 ondalıkla göster
                    dist_s = f"{d:.1f} cm" if pd.notnull(d) else "-- cm"
                    speed_s = f"{s:.1f} cm/s" if pd.notnull(s) else "-- cm/s"
                    if buzzer_thr is not None and pd.notnull(d) and d <= buzzer_thr:
                        dist_style.update({'backgroundColor':'#d9534f', 'color':'white'})
                
                q_max = f"SELECT MAX(mesafe_cm) as max_dist FROM scan_points WHERE scan_id = {latest_id} AND mesafe_cm < 250 AND mesafe_cm > 0"
                df_max = pd.read_sql_query(q_max, conn)
                if not df_max.empty and pd.notnull(df_max['max_dist'].iloc[0]):
                    max_dist_s = f"{df_max['max_dist'].iloc[0]:.1f} cm"
        except Exception as e: print(f"Anlık değerler/max mesafe güncellenirken hata: {e}")
        finally: conn.close()
    return angle_s, dist_s, speed_s, dist_style, max_dist_s

@app.callback(
    [Output('calculated-area', 'children'),Output('perimeter-length', 'children'),Output('max-width', 'children'),Output('max-depth', 'children')],
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
                df_s = pd.read_sql_query(f"SELECT hesaplanan_alan_cm2, cevre_cm, max_genislik_cm, max_derinlik_cm FROM servo_scans WHERE id = {latest_id}", conn)
                if not df_s.empty:
                    r = df_s.iloc[0]
                    area = f"{r['hesaplanan_alan_cm2']:.2f} cm²" if pd.notnull(r['hesaplanan_alan_cm2']) else "N/A"
                    perim = f"{r['cevre_cm']:.2f} cm" if pd.notnull(r['cevre_cm']) else "N/A"
                    width = f"{r['max_genislik_cm']:.2f} cm" if pd.notnull(r['max_genislik_cm']) else "N/A"
                    depth = f"{r['max_derinlik_cm']:.2f} cm" if pd.notnull(r['max_derinlik_cm']) else "N/A"
        except Exception as e: print(f"Analiz paneli DB sorgu hatası: {e}")
        finally: conn.close()
    return area, perim, width, depth

@app.callback(
    [Output('script-status', 'children'), Output('script-status', 'className'),
     Output('cpu-usage', 'value'), Output('cpu-usage', 'label'),
     Output('ram-usage', 'value'), Output('ram-usage', 'label')],
    [Input('interval-component-system', 'n_intervals')]
)
def update_system_card(n_intervals):
    status_text, status_class = "Beklemede", "text-secondary"
    pid_val = None
    # Sensör betiğinin PID dosyasını kontrol et
    if os.path.exists(PID_FILE_PATH_FOR_DASH): # Bu dosya adı sensör betiğindekiyle aynı olmalı
        try:
            with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                pid_str = pf.read().strip()
                if pid_str: pid_val = int(pid_str)
        except (IOError, ValueError): pass # Dosya yoksa veya okunamıyorsa

    if pid_val and is_process_running(pid_val):
        status_text, status_class = f"Çalışıyor (PID: {pid_val})", "text-success"
    else:
        status_text, status_class = "Çalışmıyor", "text-danger"
    
    cpu, ram = psutil.cpu_percent(interval=0.1), psutil.virtual_memory().percent
    return status_text, status_class, cpu, f"{cpu:.1f}%", ram, f"{ram:.1f}%"

# --- VERİ İNDİRME VE TABLO CALLBACK'LERİ ---
@app.callback(Output('download-csv', 'data'), [Input('export-csv-button', 'n_clicks')], prevent_initial_call=True)
def export_csv_callback(n_clicks):
    if n_clicks == 0: return no_update
    conn, _ = get_db_connection()
    if not conn: return no_update # Hata mesajı UI'da gösterilebilir
    try:
        latest_id = get_latest_scan_id_from_db(conn)
        if latest_id:
            df = pd.read_sql_query(f"SELECT * FROM scan_points WHERE scan_id = {latest_id} ORDER BY id ASC", conn)
            if not df.empty:
                return dcc.send_data_frame(df.to_csv, f"tarama_id_{latest_id}_noktalar.csv", index=False)
    except Exception as e: print(f"CSV dışa aktarma hatası: {e}")
    finally:
        if conn: conn.close()
    return no_update

@app.callback(Output('download-excel', 'data'), [Input('export-excel-button', 'n_clicks')], prevent_initial_call=True)
def export_excel_callback(n_clicks):
    if n_clicks == 0: return no_update
    conn, _ = get_db_connection()
    if not conn: return no_update
    try:
        latest_id = get_latest_scan_id_from_db(conn)
        if latest_id:
            df_pts = pd.read_sql_query(f"SELECT * FROM scan_points WHERE scan_id = {latest_id} ORDER BY id ASC", conn)
            df_info = pd.read_sql_query(f"SELECT * FROM servo_scans WHERE id = {latest_id}", conn)
            if df_pts.empty and df_info.empty: return no_update # İndirecek veri yok
            
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
                if not df_pts.empty: df_pts.to_excel(writer, sheet_name=f'Scan_{latest_id}_Points', index=False)
                if not df_info.empty: df_info.to_excel(writer, sheet_name=f'Scan_{latest_id}_Info', index=False)
            buf.seek(0)
            return dcc.send_bytes(buf.read(), f"tarama_detaylari_id_{latest_id}.xlsx")
    except Exception as e: print(f"Excel dışa aktarma hatası: {e}")
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
        if not conn: return dbc.Alert(f"Veritabanı bağlantısı kurulamadı: {error}", color="danger")
        try:
            latest_id = get_latest_scan_id_from_db(conn)
            if not latest_id: return html.P("Henüz görüntülenecek tarama verisi yok.")
            query = f"SELECT id, derece, mesafe_cm, hiz_cm_s, x_cm, y_cm, timestamp FROM scan_points WHERE scan_id = {latest_id} ORDER BY id DESC"
            df = pd.read_sql_query(query, conn)
            if df.empty: return html.P(f"Tarama ID {latest_id} için veri bulunamadı.")
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s').dt.strftime('%Y-%m-%d %H:%M:%S.%f').str[:-3]
            return dash_table.DataTable(
                data=df.to_dict('records'),
                columns=[{"name": i.replace("_", " ").title(), "id": i} for i in df.columns],
                style_cell={'textAlign': 'left', 'padding': '5px'},
                style_header={'backgroundColor': 'rgb(230,230,230)', 'fontWeight': 'bold'},
                style_table={'height': '70vh', 'overflowY': 'auto', 'overflowX': 'auto'},
                page_size=20,
                sort_action="native",
                filter_action="native",
            )
        except Exception as e: return dbc.Alert(f"Tablo oluşturulurken hata oluştu: {e}", color="danger")
        finally:
            if conn: conn.close()
    return None

# --- ANA GÖRSEL GÜNCELLEME CALLBACK'İ ---
@app.callback(
    [Output('scan-map-graph', 'figure'), Output('polar-regression-graph', 'figure'),
     Output('polar-graph', 'figure'), Output('time-series-graph', 'figure'),
     Output('environment-estimation-text', 'children'), Output('clustered-data-store', 'data')],
    [Input('interval-component-main', 'n_intervals')]
)
def update_all_graphs(n_intervals):
    figs = [go.Figure() for _ in range(4)] # fig_map, fig_polar_reg, fig_polar, fig_time
    est_cart, est_polar, clear_path = "Veri bekleniyor...", "Veri bekleniyor...", ""
    id_plot, conn, store_data = None, None, None
    try:
        conn, err_conn = get_db_connection()
        if conn and not err_conn:
            id_plot = get_latest_scan_id_from_db(conn)
            if id_plot:
                df_pts = pd.read_sql_query(f"SELECT x_cm, y_cm, derece, mesafe_cm, timestamp FROM scan_points WHERE scan_id = {id_plot} ORDER BY derece ASC", conn)
                if not df_pts.empty:
                    df_val = df_pts[(df_pts['mesafe_cm'] > 1.0) & (df_pts['mesafe_cm'] < 250.0)].copy() # Geçerli aralıktaki veriler
                    if len(df_val) >= 2:
                        add_sensor_position(figs[0]); add_scan_rays(figs[0], df_val); add_sector_area(figs[0], df_val)
                        est_cart, df_clus = analyze_environment_shape(figs[0], df_val) # Bu fonksiyon fig[0]'ı günceller
                        store_data = df_clus.to_json(orient='split')
                        
                        line_data, est_polar = analyze_polar_regression(df_val)
                        figs[1].add_trace(go.Scatter(x=df_val['derece'], y=df_val['mesafe_cm'], mode='markers', name='Noktalar'))
                        if line_data: figs[1].add_trace(go.Scatter(x=line_data['x'], y=line_data['y'], mode='lines', name='Regresyon', line=dict(color='red',width=3)))
                        
                        clear_path = find_clearest_path(df_val)
                        update_polar_graph(figs[2], df_val)
                        update_time_series_graph(figs[3], df_val)
                    else: # Yeterli geçerli nokta yoksa
                        est_cart = "Analiz için yeterli geçerli nokta bulunamadı."
                        add_sensor_position(figs[0]) # Sadece sensörü göster
                else: # Tarama ID için nokta bulunamadıysa
                    est_cart = f"Tarama ID {id_plot} için nokta bulunamadı."
                    add_sensor_position(figs[0])
            else: # Hiç tarama ID'si bulunamadıysa
                est_cart = "Tarama başlatın veya mevcut bir tarama seçin."
                add_sensor_position(figs[0])
        else: # Veritabanı bağlantı hatası
            est_cart = f"Veritabanı bağlantı hatası: {err_conn}"
            add_sensor_position(figs[0]) # Hata durumunda bile sensörü göster

    except Exception as e:
        import traceback
        print(f"KRİTİK HATA: Grafikleme sırasında: {e}\n{traceback.format_exc()}")
        est_cart = f"Kritik Grafikleme Hatası: {e}"
        add_sensor_position(figs[0]) # Hata durumunda bile sensörü göster
    finally:
        if conn: conn.close()

    # Figür başlıkları ve ortak layout ayarları
    titles = ['Ortamın 2D Haritası (Analizli)', 'Açıya Göre Mesafe Regresyonu', 'Polar Grafik', 'Zaman Serisi - Mesafe']
    common_legend = dict(orientation="h",yanchor="bottom",y=1.02,xanchor="right",x=1, bgcolor="rgba(255,255,255,0.7)", bordercolor="Black", borderwidth=1)
    
    for i, fig in enumerate(figs):
        fig.update_layout(title_text=titles[i], uirevision=id_plot or 'initial_load', legend=common_legend) # uirevision zoomu korur
        if i==0: fig.update_layout(xaxis_title="Yatay Mesafe (cm)", yaxis_title="Dikey Mesafe (cm)", yaxis_scaleanchor="x", yaxis_scaleratio=1)
        elif i==1: fig.update_layout(xaxis_title="Açı (Derece)", yaxis_title="Mesafe (cm)")
            
    final_est_text = html.Div([
        html.P(clear_path, className="fw-bold text-primary", style={'fontSize': '1.1em'}),
        html.Hr(),
        html.P(est_cart),
        html.Hr(),
        html.P(est_polar)
    ])
    return figs[0], figs[1], figs[2], figs[3], final_est_text, store_data

# --- ETKİLEŞİMLİ KÜME BİLGİSİ MODAL CALLBACK'İ ---
@app.callback(
    [Output("cluster-info-modal", "is_open"), Output("modal-title", "children"), Output("modal-body", "children")],
    [Input("scan-map-graph", "clickData")],
    [State("clustered-data-store", "data")],
    prevent_initial_call=True,
)
def display_cluster_info(clickData, stored_data):
    if not clickData or not stored_data: return False, no_update, no_update
    try:
        df_clus = pd.read_json(stored_data, orient='split')
        if 'cluster' not in df_clus.columns: return False, "Hata", "Küme verisi ('cluster' sütunu) bulunamadı."
        
        clicked_point_data = clickData["points"][0]
        cluster_label_from_customdata = clicked_point_data.get('customdata') # customdata'dan küme etiketini al

        if cluster_label_from_customdata is not None:
            cluster_label = cluster_label_from_customdata
        else: # Eğer customdata yoksa (örn: ışın veya sektör alanına tıklandıysa), en yakın noktayı bul
            clicked_x, clicked_y = clicked_point_data["x"], clicked_point_data["y"] # Plotly x, y
            distances = np.sqrt((df_clus['y_cm'] - clicked_x)**2 + (df_clus['x_cm'] - clicked_y)**2)
            if distances.empty: return False, "Hata", "En yakın nokta için mesafe hesaplanamadı."
            cluster_label = df_clus.loc[distances.idxmin()]['cluster']

        if cluster_label == -1: title, body = "Gürültü Noktası", "Bu nokta bir kümeye ait değil (gürültü)."
        elif cluster_label == -2: title, body = "Analiz Yapılamadı", "Bu bölge için analiz yapılamadı (yetersiz veri)."
        else:
            cluster_df_specific = df_clus[df_clus['cluster'] == cluster_label]
            n_pts = len(cluster_df_specific)
            w = (cluster_df_specific['y_cm'].max()-cluster_df_specific['y_cm'].min()) if n_pts > 0 else 0
            d = (cluster_df_specific['x_cm'].max()-cluster_df_specific['x_cm'].min()) if n_pts > 0 else 0
            title = f"Küme #{int(cluster_label)} Detayları"
            body = html.Div([
                html.P(f"Nokta Sayısı: {n_pts}"),
                html.P(f"Yaklaşık Genişlik (Yatay): {w:.1f} cm"),
                html.P(f"Yaklaşık Derinlik (Dikey): {d:.1f} cm")
            ])
        return True, title, body
    except Exception as e:
        import traceback
        print(f"Modal HATA: {e}\n{traceback.format_exc()}")
        return True, "Hata", f"Küme bilgisi gösterilemedi: {e}"

