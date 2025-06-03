from django_plotly_dash import DjangoDash
from dash import html, dcc, Output, Input, State, no_update, dash_table
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import matplotlib.pyplot as plt
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
from sklearn.linear_model import RANSACRegressor
from sklearn.cluster import DBSCAN

from google import genai

from dotenv import load_dotenv

load_dotenv()

google_api_key = os.getenv("GOOGLE_API_KEY")

# ==============================================================================
# --- SABİTLER VE UYGULAMA BAŞLATMA ---
# ==============================================================================
try:
    PROJECT_ROOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
except NameError:
    PROJECT_ROOT_DIR = os.getcwd()

DB_FILENAME = 'live_scan_data.sqlite3'
DB_PATH = os.path.join(PROJECT_ROOT_DIR, DB_FILENAME)
SENSOR_SCRIPT_FILENAME = 'sensor_script.py'
SENSOR_SCRIPT_PATH = os.path.join(PROJECT_ROOT_DIR, SENSOR_SCRIPT_FILENAME)
SENSOR_SCRIPT_LOCK_FILE = '/tmp/sensor_scan_script.lock'
SENSOR_SCRIPT_PID_FILE = '/tmp/sensor_scan_script.pid'

DEFAULT_UI_SCAN_DURATION_ANGLE = 270.0
DEFAULT_UI_SCAN_STEP_ANGLE = 10.0
DEFAULT_UI_BUZZER_DISTANCE = 10
DEFAULT_UI_INVERT_MOTOR = False
DEFAULT_UI_STEPS_PER_REVOLUTION = 4096

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
                {'label': 'Gemini', 'value': 'gemini-2.0-flash'},
                # Gelecekte eklenebilecek diğer modeller buraya
            ],

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
                        dbc.Col(
                            [html.H6("Çevre Uzunluğu:"), html.H4(id='perimeter-length', children="-- cm")])]),
                dbc.Row([dbc.Col(
                    [html.H6("Max Genişlik:"), html.H4(id='max-width', children="-- cm")]),
                    dbc.Col([html.H6("Max Derinlik:"),
                             html.H4(id='max-depth', children="-- cm")])],
                    className="mt-2")
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
    [dbc.Tab(dcc.Graph(id='scan-map-graph', style={'height': '75vh'}), label="2D Kartezyen Harita", tab_id="tab-map"),
     dbc.Tab(dcc.Graph(id='polar-regression-graph', style={'height': '75vh'}), label="Regresyon Analizi",
             tab_id="tab-regression"),
     dbc.Tab(dcc.Graph(id='polar-graph', style={'height': '75vh'}), label="Polar Grafik", tab_id="tab-polar"),
     dbc.Tab(dcc.Graph(id='time-series-graph', style={'height': '75vh'}), label="Zaman Serisi (Mesafe)",
             tab_id="tab-time"),
     dbc.Tab(dcc.Loading(id="loading-datatable", children=[html.Div(id='tab-content-datatable')]), label="Veri Tablosu",
             tab_id="tab-datatable")],
    id="visualization-tabs-main", active_tab="tab-map")

app.layout = dbc.Container(fluid=True, children=[
    title_card,
    dbc.Row([
        dbc.Col([control_panel, dbc.Row(html.Div(style={"height": "15px"})), stats_panel,
                 dbc.Row(html.Div(style={"height": "15px"})), system_card, dbc.Row(html.Div(style={"height": "15px"})),
                 export_card], md=4, className="mb-3"),
        dbc.Col([visualization_tabs, dbc.Row(html.Div(style={"height": "15px"})),
                 dbc.Row([dbc.Col(analysis_card, md=8), dbc.Col([estimation_card], md=4)]),
                 # Bu satırda md=12 yapabilirsiniz
                 dbc.Row([  # Yeni row for AI yorumu
                     dbc.Col([
                         dbc.Card([
                             dbc.CardHeader("Akıllı Yorumlama (Yapay Zeka)", className="bg-info text-white"),
                             dbc.CardBody(html.Div([
                                 html.P("Yorum için bir model seçtikten sonra analiz başlayacaktır."),
                                 html.P(
                                     "Yoğun kullanımda, Gemini API'sinin ücretsiz kotası nedeniyle yorumların alınması birkaç saniye sürebilir veya geçici olarak yanıt vermeyebilir. Lütfen kullanımınızı kontrol edin."
                                 ),
                                 html.Div(id='ai-yorum-sonucu', className="text-center mt-2")
                             ]))
                         ], className="mt-3")
                     ], md=8)  # analysis_card ile aynı sütun genişliğinde
                 ], className="mt-3")], md=8)
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


def get_db_connection():
    try:
        if not os.path.exists(DB_PATH): return None, f"Veritabanı dosyası ({DB_PATH}) bulunamadı."
        conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn, None
    except Exception as e:
        return None, f"DB Bağlantı Hatası: {e}"


def get_latest_scan_id_from_db(conn_param=None):
    internal_conn, conn_to_use, latest_id = False, conn_param, None
    if not conn_to_use:
        conn_to_use, error = get_db_connection()
        if error: print(f"DB Bağlantı Hatası (get_latest_scan_id): {error}"); return None
        internal_conn = True
    if conn_to_use:
        try:
            df_r = pd.read_sql_query(
                "SELECT id FROM servo_scans WHERE status = 'running' ORDER BY start_time DESC LIMIT 1", conn_to_use)
            if not df_r.empty:
                latest_id = int(df_r['id'].iloc[0])
            else:
                df_l = pd.read_sql_query("SELECT id FROM servo_scans ORDER BY start_time DESC LIMIT 1", conn_to_use)
                if not df_l.empty: latest_id = int(df_l['id'].iloc[0])
        except Exception as e:
            print(f"Son tarama ID alınırken hata: {e}");
            latest_id = None
        finally:
            if internal_conn and conn_to_use: conn_to_use.close()
    return latest_id


def add_scan_rays(fig, df):
    x_lines, y_lines = [], []
    for _, row in df.iterrows(): x_lines.extend([0, row['y_cm'], None]); y_lines.extend([0, row['x_cm'], None])
    fig.add_trace(
        go.Scatter(x=x_lines, y=y_lines, mode='lines', line=dict(color='rgba(255,100,100,0.4)', dash='dash', width=1),
                   showlegend=False))


def add_sector_area(fig, df):
    poly_x, poly_y = df['y_cm'].tolist(), df['x_cm'].tolist()
    fig.add_trace(
        go.Scatter(x=[0] + poly_x, y=[0] + poly_y, mode='lines', fill='toself', fillcolor='rgba(255,0,0,0.15)',
                   line=dict(color='rgba(255,0,0,0.4)'), name='Taranan Sektör'))


def add_sensor_position(fig):
    fig.add_trace(
        go.Scatter(x=[0], y=[0], mode='markers', marker=dict(size=12, symbol='circle', color='red'), name='Sensör'))


def update_polar_graph(fig, df):
    fig.add_trace(go.Scatterpolar(r=df['mesafe_cm'], theta=df['derece'], mode='lines+markers', name='Mesafe'))
    fig.update_layout(polar=dict(radialaxis=dict(visible=True, range=[0, 250]),
                                 angularaxis=dict(direction="clockwise", period=360, thetaunit="degrees")))


def update_time_series_graph(fig, df):
    if df.empty:
        fig.add_trace(go.Scatter(x=[], y=[], mode='lines', name='Veri Yok'))
    else:
        try:
            df_s = df.sort_values(by='timestamp')
            datetime_x = pd.to_datetime(df_s['timestamp'], unit='s', errors='coerce')

            valid_indices = datetime_x.notnull()
            datetime_x_valid = datetime_x[valid_indices]
            df_s_valid = df_s[valid_indices]

            if df_s_valid.empty:
                fig.add_trace(go.Scatter(x=[], y=[], mode='lines', name='Geçerli Zaman Verisi Yok'))
            else:
                fig.add_trace(
                    go.Scatter(x=datetime_x_valid, y=df_s_valid['mesafe_cm'], mode='lines+markers', name='Mesafe'))

        except Exception as e:
            print(f"Zaman serisi grafiği oluşturulurken HATA: {e}")
            fig.add_trace(go.Scatter(x=[], y=[], mode='lines', name='Grafik Hatası'))

    fig.update_layout(
        xaxis_title="Zaman",
        yaxis_title="Mesafe (cm)",
        xaxis=dict(
            tickformat='%H:%M:%S',  # Saat:Dakika:Saniye formatı
            # nticks=10 # İsteğe bağlı olarak tick sayısını belirleyebilirsiniz
        )
    )


def find_clearest_path(df_valid):
    if df_valid.empty: return "En açık yol için veri yok."
    try:
        df_filtered = df_valid[df_valid['mesafe_cm'] > 0]
        if df_filtered.empty: return "Geçerli pozitif mesafe bulunamadı."
        cp = df_filtered.loc[df_filtered['mesafe_cm'].idxmax()]
        return f"En Açık Yol: {cp['derece']:.1f}° yönünde, {cp['mesafe_cm']:.1f} cm."
    except Exception:
        return "En açık yol hesaplanamadı."


def analyze_polar_regression(df_valid):
    if len(df_valid) < 5: return None, "Polar regresyon için yetersiz veri."
    X, y = df_valid[['derece']].values, df_valid['mesafe_cm'].values
    try:
        ransac = RANSACRegressor(random_state=42);
        ransac.fit(X, y)
        slope = ransac.estimator_.coef_[0]
        inf = f"Yüzey dairesel/paralel (Eğim:{slope:.3f})" if abs(slope) < 0.1 else (
            f"Yüzey açı arttıkça uzaklaşıyor (Eğim:{slope:.3f})" if slope > 0 else f"Yüzey açı arttıkça yaklaşıyor (Eğim:{slope:.3f})")
        xr = np.array([df_valid['derece'].min(), df_valid['derece'].max()]).reshape(-1, 1)
        return {'x': xr.flatten(), 'y': ransac.predict(xr)}, "Polar Regresyon: " + inf
    except Exception:
        return None, "Polar regresyon hatası."


def analyze_environment_shape(fig, df_valid):
    points_all = df_valid[['y_cm', 'x_cm']].to_numpy()
    if len(points_all) < 10: df_valid['cluster'] = -2; return "Analiz için yetersiz veri.", df_valid
    db = DBSCAN(eps=15, min_samples=3).fit(points_all)
    df_valid['cluster'] = db.labels_
    desc, unique_clusters = [], set(db.labels_)
    num_actual_clusters = len(unique_clusters - {-1})
    desc.append(
        f"{num_actual_clusters} potansiyel nesne kümesi bulundu." if num_actual_clusters > 0 else "Belirgin bir nesne kümesi bulunamadı (DBSCAN).")
    colors = plt.cm.get_cmap('viridis', len(unique_clusters) if len(unique_clusters) > 0 else 1)
    for k_label in unique_clusters:
        cluster_points = points_all[db.labels_ == k_label]
        if k_label == -1:
            color_val, point_size, name_val = 'rgba(128,128,128,0.3)', 5, 'Gürültü/Diğer'
        else:
            norm_k = (k_label / (len(unique_clusters - {-1}) - 1)) if len(unique_clusters - {-1}) > 1 else 0.0
            raw_col = colors(np.clip(norm_k, 0.0, 1.0))
            color_val = f'rgba({raw_col[0] * 255:.0f},{raw_col[1] * 255:.0f},{raw_col[2] * 255:.0f},0.9)'
            point_size, name_val = 8, f'Küme {k_label}'
        fig.add_trace(go.Scatter(x=cluster_points[:, 0], y=cluster_points[:, 1], mode='markers',
                                 marker=dict(color=color_val, size=point_size), name=name_val,
                                 customdata=[k_label] * len(cluster_points)))
    return " ".join(desc), df_valid


def estimate_geometric_shape(df):
    if len(df) < 15: return "Şekil tahmini için yetersiz nokta."
    try:
        points = df[['x_cm', 'y_cm']].values
        hull = ConvexHull(points)
        hull_area = hull.volume
        min_x, max_x = df['x_cm'].min(), df['x_cm'].max()
        min_y, max_y = df['y_cm'].min(), df['y_cm'].max()
        width, depth = max_y - min_y, max_x - min_x
        if width < 1 or depth < 1: return "Algılanan şekil çok küçük."
        bbox_area = width * depth
        fill_factor = hull_area / bbox_area if bbox_area > 0 else 0
        aspect_ratio = width / depth
        if aspect_ratio > 5 and depth < 30: return "Tahmin: Geniş ve ince bir yüzey (Duvar)."
        if aspect_ratio < 0.25 and depth > 50: return "Tahmin: Dar ve derin bir boşluk (Koridor)."
        if fill_factor > 0.80:
            return "Tahmin: Dolgun, dikdörtgensel bir nesne." if 0.8 > aspect_ratio or aspect_ratio > 1.25 else "Tahmin: Kutu veya dairesel bir nesne."
        if fill_factor < 0.4: return "Tahmin: İçbükey bir yapı (Köşe)."
        return "Tahmin: Düzensiz veya karmaşık bir yapı."
    except Exception:
        return "Geometrik analiz hatası."


def get_latest_scan_data():
    conn, _ = get_db_connection()
    if conn:
        latest_id = get_latest_scan_id_from_db(conn)
        if latest_id:
            df = pd.read_sql_query(
                f"SELECT derece, mesafe_cm FROM scan_points WHERE scan_id={latest_id} ORDER BY derece ASC", conn)
            conn.close()
            return df
        conn.close()
    return None


def yorumla_tablo_verisi_gemini(df):
    google_api_key = os.getenv("GOOGLE_API_KEY")

    if df is not None and not df.empty:

        client = genai.Client(api_key=google_api_key)

        # Veriyi Gemini'ye uygun bir formata dönüştürün (örneğin, string)
        prompt_text = "Aşağıdaki tablo robotik bir taramadan elde edilen açı (derece) ve mesafe (cm) verilerini içermektedir:\n"
        prompt_text += df.to_string(index=False)
        prompt_text += "\nBu verilere göre ortamı yorumlayın ve olası nesneler hakkında bilgi verin."

        try:
            try:
                response = client.models.generate_content(
                    model='gemini-2.0-flash',
                    contents=prompt_text,
                )
                return response.text
            except Exception as e:
                return f"Gemini'den yanıt alınırken hata oluştu: {e}"
        except Exception as e:
            return f"Gemini'den yanıt alınırken hata oluştu: {e}"
    else:
        return "Yorumlanacak tablo verisi bulunamadı."


# ==============================================================================
# --- CALLBACK FONKSİYONLARI ---
# ==============================================================================
@app.callback(
    Output('scan-status-message', 'children'),
    [Input('start-scan-button', 'n_clicks')],
    [State('scan-duration-angle-input', 'value'),
     State('step-angle-input', 'value'),
     State('buzzer-distance-input', 'value'),
     State('invert-motor-checkbox', 'value'),
     State('steps-per-rev-input', 'value')],
    prevent_initial_call=True)
def handle_start_scan_script(n_clicks_start, scan_duration_angle_val,
                             step_angle_val, buzzer_distance_val,
                             invert_motor_val, steps_per_rev_val):
    if n_clicks_start == 0: return no_update
    scan_duration_a = scan_duration_angle_val if scan_duration_angle_val is not None else DEFAULT_UI_SCAN_DURATION_ANGLE
    step_a = step_angle_val if step_angle_val is not None else DEFAULT_UI_SCAN_STEP_ANGLE
    buzzer_d = buzzer_distance_val if buzzer_distance_val is not None else DEFAULT_UI_BUZZER_DISTANCE
    invert_dir = bool(invert_motor_val)
    steps_per_rev = steps_per_rev_val if steps_per_rev_val is not None else DEFAULT_UI_STEPS_PER_REVOLUTION
    if not (10 <= scan_duration_a <= 720): return dbc.Alert("Tarama Açısı 10-720 derece arasında olmalı!",
                                                            color="danger")
    if not (0.1 <= abs(step_a) <= 45): return dbc.Alert("Adım açısı 0.1-45 arasında olmalı!", color="danger")
    if not (0 <= buzzer_d <= 200): return dbc.Alert("Buzzer mesafesi 0-200cm arasında olmalı!", color="danger")
    if not (500 <= steps_per_rev <= 10000): return dbc.Alert("Motor Adım/Tur değeri 500-10000 arasında olmalı!",
                                                             color="danger")
    pid = None
    if os.path.exists(SENSOR_SCRIPT_PID_FILE):
        try:
            with open(SENSOR_SCRIPT_PID_FILE, 'r') as pf:
                pid_str = pf.read().strip();
                pid = int(pid_str) if pid_str else None
        except:
            pid = None
    if pid and is_process_running(pid): return dbc.Alert(f"Sensör betiği çalışıyor (PID:{pid}). Önce durdurun.",
                                                         color="warning")
    for fp in [SENSOR_SCRIPT_LOCK_FILE, SENSOR_SCRIPT_PID_FILE]:
        if os.path.exists(fp):
            try:
                os.remove(fp)
            except OSError as e:
                return dbc.Alert(f"Kalıntı dosya ({fp}) silinemedi: {e}.", color="danger")
    try:
        py_exec = sys.executable
        if not os.path.exists(SENSOR_SCRIPT_PATH): return dbc.Alert(f"Sensör betiği bulunamadı: {SENSOR_SCRIPT_PATH}",
                                                                    color="danger")
        cmd = [py_exec, SENSOR_SCRIPT_PATH, "--scan_duration_angle", str(scan_duration_a), "--step_angle", str(step_a),
               "--buzzer_distance", str(buzzer_d), "--invert_motor_direction", str(invert_dir), "--steps_per_rev",
               str(steps_per_rev)]
        log_path = os.path.join(PROJECT_ROOT_DIR, 'sensor_script.log')
        with open(log_path, 'w') as log_f:
            subprocess.Popen(cmd, start_new_session=True, stdout=log_f, stderr=log_f)
        time.sleep(2.5)
        if os.path.exists(SENSOR_SCRIPT_PID_FILE):
            with open(SENSOR_SCRIPT_PID_FILE, 'r') as pf_new:
                new_pid = pf_new.read().strip()
            return dbc.Alert(f"Tarama başlatıldı (PID:{new_pid}).", color="success")
        else:
            log_disp = f"PID dosyası ({SENSOR_SCRIPT_PID_FILE}) oluşmadı. "
            if os.path.exists(log_path):
                try:
                    with open(log_path, 'r') as f_log:
                        lines = "".join(f_log.readlines()[-10:]);
                        log_disp_detail = (lines[:500] + '...') if len(
                            lines) > 500 else lines
                    log_disp += "Logdan son satırlar:" if log_disp_detail.strip() else f"'{os.path.basename(log_path)}' boş."
                    return dbc.Alert([html.Span(log_disp), html.Br(), html.Pre(log_disp_detail,
                                                                               style={'whiteSpace': 'pre-wrap',
                                                                                      'maxHeight': '150px',
                                                                                      'overflowY': 'auto',
                                                                                      'fontSize': '0.8em',
                                                                                      'backgroundColor': '#f0f0f0',
                                                                                      'border': '1px solid #ccc',
                                                                                      'padding': '5px'})],
                                     color="danger") if log_disp_detail.strip() else dbc.Alert(log_disp, color="danger")
                except Exception as e_l:
                    log_disp += f"Log okuma hatası: {e_l}"
            else:
                log_disp += f"Log dosyası ('{os.path.basename(log_path)}') bulunamadı."
            return dbc.Alert(log_disp, color="danger")
    except Exception as e:
        return dbc.Alert(f"Sensör betiği başlatma hatası: {e}", color="danger")


@app.callback(Output('scan-status-message', 'children', allow_duplicate=True), [Input('stop-scan-button', 'n_clicks')],
              prevent_initial_call=True)
def handle_stop_scan_script(n):
    if n == 0: return no_update
    pid_kill = None
    if os.path.exists(SENSOR_SCRIPT_PID_FILE):
        try:
            with open(SENSOR_SCRIPT_PID_FILE, 'r') as pf:
                pid_s = pf.read().strip();
                pid_kill = int(pid_s) if pid_s else None
        except (IOError, ValueError):
            pid_kill = None
    if pid_kill and is_process_running(pid_kill):
        try:
            os.kill(pid_kill, signal.SIGTERM);
            time.sleep(2)
            if is_process_running(pid_kill): os.kill(pid_kill, signal.SIGKILL); time.sleep(0.5)
            if not is_process_running(pid_kill):
                for fp in [SENSOR_SCRIPT_PID_FILE, SENSOR_SCRIPT_LOCK_FILE]:
                    if os.path.exists(fp): os.remove(fp)
                return dbc.Alert(f"Sensör betiği (PID:{pid_kill}) durduruldu.", color="info")
            else:
                return dbc.Alert(f"Sensör betiği (PID:{pid_kill}) durdurulamadı!", color="danger")
        except ProcessLookupError:
            for fp in [SENSOR_SCRIPT_PID_FILE, SENSOR_SCRIPT_LOCK_FILE]:
                if os.path.exists(fp): os.remove(fp)
            return dbc.Alert(f"Sensör betiği (PID:{pid_kill}) zaten çalışmıyordu.", color="warning")
        except Exception as e:
            return dbc.Alert(f"Sensör betiği durdurma hatası:{e}", color="danger")
    else:
        for fp in [SENSOR_SCRIPT_PID_FILE, SENSOR_SCRIPT_LOCK_FILE]:
            if os.path.exists(fp):
                try:
                    os.remove(fp)
                except OSError:
                    pass
        return dbc.Alert("Çalışan sensör betiği bulunamadı.", color="warning")


@app.callback(
    [Output('current-angle', 'children'), Output('current-distance', 'children'), Output('current-speed', 'children'),
     Output('current-distance-col', 'style'), Output('max-detected-distance', 'children')],
    [Input('interval-component-main', 'n_intervals')])
def update_realtime_values(n):
    conn, err = get_db_connection()
    angle_s, dist_s, speed_s, max_dist_s = "--°", "-- cm", "-- cm/s", "-- cm"
    dist_style = {'padding': '10px', 'transition': 'background-color 0.5s ease', 'borderRadius': '5px'}
    if err or not conn: return angle_s, dist_s, speed_s, dist_style, max_dist_s
    try:
        lid = get_latest_scan_id_from_db(conn)
        if lid:
            df_p = pd.read_sql_query(
                f"SELECT mesafe_cm,derece,hiz_cm_s FROM scan_points WHERE scan_id={lid} ORDER BY id DESC LIMIT 1", conn)
            df_set = pd.read_sql_query(f"SELECT buzzer_distance_setting FROM servo_scans WHERE id={lid}", conn)
            b_thr = float(df_set['buzzer_distance_setting'].iloc[0]) if not df_set.empty and pd.notnull(
                df_set['buzzer_distance_setting'].iloc[0]) else None
            if not df_p.empty:
                d, a, s = df_p['mesafe_cm'].iloc[0], df_p['derece'].iloc[0], df_p['hiz_cm_s'].iloc[0]
                angle_s, dist_s, speed_s = f"{a:.1f}°" if pd.notnull(a) else "--°", f"{d:.1f} cm" if pd.notnull(
                    d) else "-- cm", f"{s:.1f} cm/s" if pd.notnull(s) else "-- cm/s"
                if b_thr is not None and pd.notnull(d) and d <= b_thr: dist_style.update(
                    {'backgroundColor': '#d9534f', 'color': 'white'})
            df_max = pd.read_sql_query(
                f"SELECT MAX(mesafe_cm) as max_dist FROM scan_points WHERE scan_id={lid} AND mesafe_cm<250 AND mesafe_cm>0",
                conn)
            if not df_max.empty and pd.notnull(
                    df_max['max_dist'].iloc[0]): max_dist_s = f"{df_max['max_dist'].iloc[0]:.1f} cm"
    finally:
        if conn: conn.close()
    return angle_s, dist_s, speed_s, dist_style, max_dist_s


@app.callback(
    [Output('calculated-area', 'children'), Output('perimeter-length', 'children'), Output('max-width', 'children'),
     Output('max-depth', 'children')], [Input('interval-component-main', 'n_intervals')])
def update_analysis_panel(n):
    conn, err = get_db_connection()
    area, perim, width, depth = "-- cm²", "-- cm", "-- cm", "-- cm"
    if err or not conn: return area, perim, width, depth
    try:
        lid = get_latest_scan_id_from_db(conn)
        if lid:
            df_s = pd.read_sql_query(
                f"SELECT hesaplanan_alan_cm2,cevre_cm,max_genislik_cm,max_derinlik_cm FROM servo_scans WHERE id={lid}",
                conn)
            if not df_s.empty:
                r = df_s.iloc[0]
                area = f"{r['hesaplanan_alan_cm2']:.2f} cm²" if pd.notnull(r['hesaplanan_alan_cm2']) else "N/A"
                perim = f"{r['cevre_cm']:.2f} cm" if pd.notnull(r['cevre_cm']) else "N/A"
                width = f"{r['max_genislik_cm']:.2f} cm" if pd.notnull(r['max_genislik_cm']) else "N/A"
                depth = f"{r['max_derinlik_cm']:.2f} cm" if pd.notnull(r['max_derinlik_cm']) else "N/A"
    finally:
        if conn: conn.close()
    return area, perim, width, depth


@app.callback([Output('script-status', 'children'), Output('script-status', 'className'), Output('cpu-usage', 'value'),
               Output('cpu-usage', 'label'), Output('ram-usage', 'value'), Output('ram-usage', 'label')],
              [Input('interval-component-system', 'n_intervals')])
def update_system_card(n):
    stat_txt, stat_cls, pid_v = "Beklemede", "text-secondary", None
    if os.path.exists(SENSOR_SCRIPT_PID_FILE):
        try:
            with open(SENSOR_SCRIPT_PID_FILE, 'r') as pf:
                pid_v = int(pf.read().strip()) if pf.read().strip() else None
        except:
            pass
    if pid_v and is_process_running(pid_v):
        stat_txt, stat_cls = f"Çalışıyor (PID:{pid_v})", "text-success"
    else:
        stat_txt, stat_cls = "Çalışmıyor", "text-danger"
    cpu, ram = psutil.cpu_percent(.1), psutil.virtual_memory().percent
    return stat_txt, stat_cls, cpu, f"{cpu:.1f}%", ram, f"{ram:.1f}%"


@app.callback(Output('download-csv', 'data'), [Input('export-csv-button', 'n_clicks')], prevent_initial_call=True)
def export_csv_callback(n):
    if n == 0: return no_update
    conn, _ = get_db_connection()
    if not conn: return no_update
    try:
        lid = get_latest_scan_id_from_db(conn)
        if lid:
            df = pd.read_sql_query(f"SELECT * FROM scan_points WHERE scan_id={lid} ORDER BY id ASC", conn)
            if not df.empty: return dcc.send_data_frame(df.to_csv, f"tarama_id_{lid}_noktalar.csv", index=False)
    finally:
        if conn: conn.close()
    return no_update


@app.callback(Output('download-excel', 'data'), [Input('export-excel-button', 'n_clicks')], prevent_initial_call=True)
def export_excel_callback(n):
    if n == 0: return no_update
    conn, _ = get_db_connection()
    if not conn: return no_update
    try:
        lid = get_latest_scan_id_from_db(conn)
        if lid:
            df_p = pd.read_sql_query(f"SELECT * FROM scan_points WHERE scan_id={lid} ORDER BY id ASC", conn)
            df_i = pd.read_sql_query(f"SELECT * FROM servo_scans WHERE id={lid}", conn)
            if df_p.empty and df_i.empty: return no_update
            with io.BytesIO() as buf, pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
                if not df_p.empty: df_p.to_excel(writer, sheet_name=f'Scan_{lid}_Points', index=False)
                if not df_i.empty: df_i.to_excel(writer, sheet_name=f'Scan_{lid}_Info', index=False)
            return dcc.send_bytes(buf.getvalue(), f"tarama_detaylari_id_{lid}.xlsx")
    finally:
        if conn: conn.close()
    return no_update


@app.callback(Output('tab-content-datatable', 'children'),
              [Input('visualization-tabs-main', 'active_tab'), Input('interval-component-main', 'n_intervals')])
def render_and_update_data_table(active_tab, n):
    if active_tab != "tab-datatable": return None
    conn, err = get_db_connection()
    if err or not conn: return dbc.Alert(f"DB bağlantı hatası:{err}", color="danger")
    try:
        lid = get_latest_scan_id_from_db(conn)
        if not lid: return html.P("Görüntülenecek veri yok.")
        df = pd.read_sql_query(
            f"SELECT id,derece,mesafe_cm,hiz_cm_s,x_cm,y_cm,timestamp FROM scan_points WHERE scan_id={lid} ORDER BY id DESC",
            conn)
        if df.empty: return html.P(f"Tarama ID {lid} için veri yok.")
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s').dt.strftime('%Y-%m-%d %H:%M:%S.%f').str[:-3]
        return dash_table.DataTable(data=df.to_dict('records'),
                                    columns=[{"name": i.replace("_", " ").title(), "id": i} for i in df.columns],
                                    style_cell={'textAlign': 'left', 'padding': '5px'},
                                    style_header={'backgroundColor': 'rgb(230,230,230)', 'fontWeight': 'bold'},
                                    style_table={'height': '70vh', 'overflowY': 'auto', 'overflowX': 'auto'},
                                    page_size=20, sort_action="native", filter_action="native")
    except Exception as e:
        return dbc.Alert(f"Tablo oluşturulurken hata: {e}", color="danger")
    finally:
        if conn: conn.close()


@app.callback(
    [
        Output('scan-map-graph', 'figure'),
        Output('polar-regression-graph', 'figure'),
        Output('polar-graph', 'figure'),
        Output('time-series-graph', 'figure'),
        Output('environment-estimation-text', 'children')
    ],
    [Input('interval-component-main', 'n_intervals')]
)
def update_all_graphs(n):
    figs = [go.Figure() for _ in range(4)]
    est_cart, est_polar, clear_path, shape_estimation = "Veri bekleniyor...", "Veri bekleniyor...", "", "Veri bekleniyor..."
    id_plot, store_data = None, None
    conn, err_conn = get_db_connection()
    if err_conn or not conn:
        est_cart = f"DB Bağlantı Hatası: {err_conn}"
    else:
        try:
            id_plot = get_latest_scan_id_from_db(conn)
            if id_plot:
                df_pts = pd.read_sql_query(
                    f"SELECT x_cm,y_cm,derece,mesafe_cm,timestamp FROM scan_points WHERE scan_id={id_plot} ORDER BY derece ASC",
                    conn)
                if not df_pts.empty:
                    df_val = df_pts[(df_pts['mesafe_cm'] > 0.1) & (df_pts['mesafe_cm'] < 300.0)].copy()
                    if len(df_val) >= 2:
                        add_scan_rays(figs[0], df_val);
                        add_sector_area(figs[0], df_val)
                        est_cart, df_clus = analyze_environment_shape(figs[0], df_val)
                        store_data = df_clus.to_json(orient='split')
                        line_data, est_polar = analyze_polar_regression(df_val)
                        figs[1].add_trace(
                            go.Scatter(x=df_val['derece'], y=df_val['mesafe_cm'], mode='markers', name='Noktalar'))
                        if line_data: figs[1].add_trace(
                            go.Scatter(x=line_data['x'], y=line_data['y'], mode='lines', name='Regresyon',
                                       line=dict(color='red', width=3)))
                        clear_path, shape_estimation = find_clearest_path(df_val), estimate_geometric_shape(df_val)
                        update_polar_graph(figs[2], df_val);
                        update_time_series_graph(figs[3], df_val)

                    else:
                        est_cart = "Analiz için yetersiz geçerli nokta."
                else:
                    est_cart = f"Tarama ID {id_plot} için nokta yok."
            else:
                est_cart = "Tarama başlatın."
        except Exception as e:
            import traceback;
            est_cart = f"Grafikleme Hatası: {e}\n{traceback.format_exc()}"
        finally:
            if conn: conn.close()
    for fig_idx, fig in enumerate(figs):
        if not fig.data:
            add_sensor_position(fig)
        elif fig_idx == 0 and not any(trace.name == 'Sensör' for trace in fig.data):
            add_sensor_position(fig)
    titles = ['Ortamın 2D Haritası (Mantıksal)', 'Açıya Göre Mesafe Regresyonu (Mantıksal)', 'Polar Grafik (Mantıksal)',
              'Zaman Serisi - Mesafe']
    common_legend = dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
                         bgcolor="rgba(255,255,255,0.7)", bordercolor="Black", borderwidth=1)
    for i, fig in enumerate(figs):
        fig.update_layout(title_text=titles[i], uirevision=str(id_plot) or 'initial_load', legend=common_legend)
        if i == 0:
            fig.update_layout(xaxis_title="Yatay Mesafe (cm) [Mantıksal 0'a göre]",
                              yaxis_title="Dikey Mesafe (cm) [Mantıksal 0'a göre]", yaxis_scaleanchor="x",
                              yaxis_scaleratio=1)
        elif i == 1:
            fig.update_layout(xaxis_title="Mantıksal Açı (Derece)", yaxis_title="Mesafe (cm)")
        elif i == 2:
            fig.update_layout(
                polar=dict(angularaxis=dict(thetaunit="degrees", rotation=90, direction="counterclockwise")))
    final_est_text = html.Div(
        [html.P(shape_estimation, className="fw-bold", style={'fontSize': '1.2em', 'color': 'darkgreen'}), html.Hr(),
         html.P(clear_path, className="fw-bold text-primary", style={'fontSize': '1.1em'}), html.Hr(),
         html.P(est_cart), html.Hr(), html.P(est_polar)])
    return figs[0], figs[1], figs[2], figs[3], final_est_text


@app.callback(
    [Output("cluster-info-modal", "is_open"), Output("modal-title", "children"), Output("modal-body", "children")],
    [Input("scan-map-graph", "clickData")], [State("clustered-data-store", "data")], prevent_initial_call=True)
def display_cluster_info(clickData, stored_data):
    if not clickData or not stored_data: return False, no_update, no_update
    try:
        df_clus = pd.read_json(stored_data, orient='split')
        if 'cluster' not in df_clus.columns: return False, "Hata", "Küme verisi bulunamadı."
        cl_label = clickData["points"][0].get('customdata')
        if cl_label is None: return False, no_update, no_update
        if cl_label == -1:
            title, body = "Gürültü Noktası", "Bu nokta bir nesne kümesine ait değil."
        elif cl_label == -2:
            title, body = "Analiz Yapılamadı", "Bu bölge için analiz yapılamadı."
        else:
            cl_df = df_clus[df_clus['cluster'] == cl_label]
            n_pts, w, d = len(cl_df), 0, 0
            if n_pts > 0: w, d = (cl_df['y_cm'].max() - cl_df['y_cm'].min()), (
                    cl_df['x_cm'].max() - cl_df['x_cm'].min())
            title = f"Küme #{int(cl_label)} Detayları"
            body = html.Div([html.P(f"Nokta Sayısı: {n_pts}"), html.P(f"Yaklaşık Genişlik: {w:.1f} cm"),
                             html.P(f"Yaklaşık Derinlik: {d:.1f} cm")])
        return True, title, body
    except Exception as e:
        return True, "Hata", f"Küme bilgisi gösterilemedi: {e}"


@app.callback(
    Output('ai-yorum-sonucu', 'children'),
    [Input('ai-model-dropdown', 'value')],
    [State('interval-component-main', 'n_intervals')],  # Belki en son veriyi almak için kullanabiliriz
    prevent_initial_call=True
)
def yorumla_model_secimi(selected_model, n):
    if selected_model == 'gemini':
        df_veri = get_latest_scan_data()
        if df_veri is not None and not df_veri.empty:
            yorum = yorumla_tablo_verisi_gemini(df_veri)
            return dbc.Alert(yorum, color="success")
        else:
            return dbc.Alert("Yorumlanacak geçerli veri bulunamadı.", color="warning")
    elif selected_model:
        return dbc.Alert(f"Seçilen model ({selected_model}) henüz desteklenmiyor.", color="info")
    return html.Div("Yorum için bir model seçin.", className="text-center")
