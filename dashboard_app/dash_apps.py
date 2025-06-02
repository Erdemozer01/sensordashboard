from django_plotly_dash import DjangoDash

from dash import html, dcc, Output, Input, State, no_update, dash_table
import dash_bootstrap_components as dbc
import plotly.graph_objects as go

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

# --- Sabitler ---
try:
    PROJECT_ROOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
except NameError:
    PROJECT_ROOT_DIR = os.getcwd()

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

# --- LAYOUT BİLEŞENLERİ ---
title_card = dbc.Row(
    [dbc.Col(html.H1("Dream Pi Kullanıcı Paneli", className="text-center my-3 mb-5"), width=12), html.Hr(), ])

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
                                  max=200,
                                  step=1)], className="mb-2"),
    ])
])

stats_panel = dbc.Card([
    dbc.CardHeader("Anlık Sensör Değerleri", className="bg-info text-white"),
    dbc.CardBody(
        dbc.Row([
            dbc.Col(html.Div([html.H6("Mevcut Açı:"), html.H4(id='current-angle', children="--°")]), width=4,
                    className="text-center"),
            dbc.Col(html.Div([html.H6("Mevcut Mesafe:"), html.H4(id='current-distance', children="-- cm")]), width=4,
                    className="text-center"),
            dbc.Col(html.Div([html.H6("Anlık Hız:"), html.H4(id='current-speed', children="-- cm/s")]), width=4,
                    className="text-center")
        ]))
], className="mb-3")

system_card = dbc.Card([
    dbc.CardHeader("Sistem Durumu", className="bg-secondary text-white"),
    dbc.CardBody([
        dbc.Row([dbc.Col(html.Div(
            [html.H6("Sensör Durumu:"), html.H5(id='script-status', children="Beklemede")]))], className="mb-2"),
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
    dbc.CardBody(
        html.H4("Tahmin: Bekleniyor...", id='environment-estimation-text', className="text-center")
    )
])

# KeyError hatasını çözmek için yapılan layout değişikliği
visualization_tabs = dbc.Tabs(
    [
        dbc.Tab(dcc.Graph(id='scan-map-graph', style={'height': '75vh'}), label="2D Kartezyen Harita",
                tab_id="tab-map"),
        dbc.Tab(dcc.Graph(id='polar-graph', style={'height': '75vh'}), label="Polar Grafik", tab_id="tab-polar"),
        dbc.Tab(dcc.Graph(id='time-series-graph', style={'height': '75vh'}), label="Zaman Serisi (Mesafe)",
                tab_id="tab-time"),
        dbc.Tab(
            dcc.Loading(id="loading-datatable", children=[html.Div(id='tab-content-datatable')]),
            label="Veri Tablosu",
            tab_id="tab-datatable",
        )
    ],
    id="visualization-tabs-main",
    active_tab="tab-map",
)

app.layout = dbc.Container(fluid=True, children=[
    title_card,
    dbc.Row([
        dbc.Col([control_panel,
                 dbc.Row(html.Div(style={"height": "15px"})), stats_panel,
                 dbc.Row(html.Div(style={"height": "15px"})), system_card,
                 dbc.Row(html.Div(style={"height": "15px"})), export_card],
                md=4, className="mb-3"),
        dbc.Col([
            visualization_tabs,
            dbc.Row(html.Div(style={"height": "15px"})),
            dbc.Row([
                dbc.Col(analysis_card, md=8),
                dbc.Col(estimation_card, md=4)
            ])
        ], md=8)
    ]),
    dcc.Interval(id='interval-component-main', interval=3000, n_intervals=0),
    dcc.Interval(id='interval-component-system', interval=3000, n_intervals=0),
])


# --- HELPER FONKSİYONLAR ---
def is_process_running(pid):
    if pid is None: return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    else:
        return True


def get_db_connection():
    try:
        if not os.path.exists(DB_PATH): return None, f"Veritabanı dosyası ({DB_PATH}) bulunamadı."
        conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, timeout=5)
        return conn, None
    except sqlite3.OperationalError as e:
        return None, f"DB Kilitli/Hata: {e}"
    except Exception as e:
        return None, f"DB Bağlantı Hatası: {e}"


def get_latest_scan_id_from_db(conn_param=None):
    internal_conn = False
    conn_to_use = conn_param
    latest_id = None
    if not conn_to_use:
        conn_to_use, error = get_db_connection()
        if error: print(f"DB Hatası (get_latest_scan_id): {error}"); return None
        internal_conn = True
    if conn_to_use:
        try:
            df_scan = pd.read_sql_query(
                "SELECT id FROM servo_scans WHERE status = 'running' ORDER BY start_time DESC LIMIT 1", conn_to_use)
            if df_scan.empty:
                df_scan = pd.read_sql_query("SELECT id FROM servo_scans ORDER BY start_time DESC LIMIT 1", conn_to_use)
            if not df_scan.empty:
                latest_id = int(df_scan['id'].iloc[0])
        except Exception as e:
            print(f"Son tarama ID alınırken hata: {e}")
        finally:
            if internal_conn and conn_to_use: conn_to_use.close()
    return latest_id


# --- GRAFİK ve ANALİZ YARDIMCI FONKSİYONLARI ---

def add_scan_rays(fig, df):
    x_lines, y_lines = [], []
    for index, row in df.iterrows():
        x_lines.extend([0, row['y_cm'], None])
        y_lines.extend([0, row['x_cm'], None])
    fig.add_trace(go.Scatter(
        x=x_lines, y=y_lines, mode='lines',
        line=dict(color='rgba(255, 100, 100, 0.4)', dash='dash', width=1),
        showlegend=False
    ))


def add_sector_area(fig, df):
    poly_x = df['y_cm'].tolist()
    poly_y = df['x_cm'].tolist()
    sector_polygon_x = [0] + poly_x
    sector_polygon_y = [0] + poly_y
    fig.add_trace(go.Scatter(
        x=sector_polygon_x, y=sector_polygon_y, mode='lines',
        fill='toself', fillcolor='rgba(255,0,0,0.15)',
        line=dict(color='rgba(255,0,0,0.4)'), name='Taranan Sektör Alanı'
    ))


def add_detected_points(fig, df):
    fig.add_trace(go.Scatter(
        x=df['y_cm'], y=df['x_cm'], mode='markers',
        marker=dict(
            color=df['angle_deg'], colorscale='Viridis', showscale=True,
            colorbar=dict(title='Açı (Derece)'), size=8
        ),
        name='Algılanan Noktalar'
    ))


def add_sensor_position(fig):
    fig.add_trace(go.Scatter(
        x=[0], y=[0], mode='markers',
        marker=dict(size=12, symbol='circle', color='red'),
        name='Sensör Pozisyonu'
    ))


def analyze_environment_shape(fig, df):
    points = df[['y_cm', 'x_cm']].to_numpy()
    X = points[:, 0].reshape(-1, 1)
    y = points[:, 1]

    if len(points) > 20:
        try:
            ransac1 = RANSACRegressor(min_samples=10)
            ransac1.fit(X, y)
            inlier_mask1 = ransac1.inlier_mask_
            outlier_mask1 = ~inlier_mask1
            if np.sum(outlier_mask1) > 10:
                ransac2 = RANSACRegressor(min_samples=10)
                ransac2.fit(X[outlier_mask1], y[outlier_mask1])
                inlier_mask2 = ransac2.inlier_mask_
                slope1 = ransac1.estimator_.coef_[0]
                slope2 = ransac2.estimator_.coef_[0]
                if abs(np.arctan(slope1) - np.arctan(slope2)) < np.deg2rad(5):
                    fig.add_trace(
                        go.Scatter(x=X[inlier_mask1].flatten(), y=ransac1.predict(X[inlier_mask1]), mode='lines',
                                   line=dict(color='cyan', width=4), name='Koridor Duvarı 1'))
                    fig.add_trace(go.Scatter(x=X[outlier_mask1][inlier_mask2].flatten(),
                                             y=ransac2.predict(X[outlier_mask1][inlier_mask2]), mode='lines',
                                             line=dict(color='cyan', width=4), name='Koridor Duvarı 2'))
                    return "Bir koridorda olabilirsin."
        except Exception as e:
            print(f"Koridor analizi hatası: {e}")

    if len(points) > 10:
        try:
            ransac_corner1 = RANSACRegressor(min_samples=5)
            ransac_corner1.fit(X, y)
            if np.sum(~ransac_corner1.inlier_mask_) > 5:
                ransac_corner2 = RANSACRegressor(min_samples=5)
                ransac_corner2.fit(X[~ransac_corner1.inlier_mask_], y[~ransac_corner1.inlier_mask_])
                slope_c1 = ransac_corner1.estimator_.coef_[0]
                slope_c2 = ransac_corner2.estimator_.coef_[0]
                angle_diff = abs(np.degrees(np.arctan(slope_c1)) - np.degrees(np.arctan(slope_c2)))
                if 80 < angle_diff < 100 or 260 < angle_diff < 280:
                    fig.add_trace(go.Scatter(x=X[ransac_corner1.inlier_mask_].flatten(),
                                             y=ransac_corner1.predict(X[ransac_corner1.inlier_mask_]), mode='lines',
                                             line=dict(color='orange', width=4), name='Oda Köşesi 1'))
                    fig.add_trace(go.Scatter(x=X[~ransac_corner1.inlier_mask_].flatten(),
                                             y=ransac_corner2.predict(X[~ransac_corner1.inlier_mask_]), mode='lines',
                                             line=dict(color='orange', width=4), name='Oda Köşesi 2'))
                    return "Dikdörtgensel bir odanın köşesindesin."
        except Exception as e:
            print(f"Köşe analizi hatası: {e}")

    if np.std(df['mesafe_cm']) < 5:
        return "Dairesel veya kavisli bir alandasın."

    if len(points) >= 3:
        hull = ConvexHull(points)
        simplified_points = simplify_coords(points[hull.vertices], 3.0)
        num_vertices = len(simplified_points) - 1
        shape_map = {3: "Üçgen bir alandasın.", 4: "Dörtgen bir alandasın.", 5: "Beşgen bir alandasın."}
        final_polygon_points = np.append(simplified_points, [simplified_points[0]], axis=0)
        fig.add_trace(go.Scatter(x=final_polygon_points[:, 0], y=final_polygon_points[:, 1],
                                 mode='lines', fill='none',
                                 line=dict(color='rgba(0, 100, 255, 0.7)', dash='dashdot', width=2),
                                 name='Genel Dış Çeper'))
        return shape_map.get(num_vertices, f"{num_vertices} köşeli genel bir alandasın.")

    return "Ortam şekli anlaşılamadı."


def update_polar_graph(fig, df):
    fig.add_trace(go.Scatterpolar(r=df['mesafe_cm'], theta=df['angle_deg'], mode='lines+markers', name='Mesafe'))
    fig.update_layout(
        polar=dict(radialaxis=dict(visible=True, range=[0, 200]), angularaxis=dict(direction="clockwise")))


def update_time_series_graph(fig, df):
    df_time_sorted = df.sort_values(by='timestamp')
    datetime_series = pd.to_datetime(df_time_sorted['timestamp'], unit='s')
    fig.add_trace(
        go.Scatter(x=datetime_series, y=df_time_sorted['mesafe_cm'], mode='lines+markers', name='Mesafe (cm)'))
    fig.update_layout(xaxis_title="Zaman", yaxis_title="Mesafe (cm)")


# --- CALLBACK FONKSİYONLARI ---

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
    if not (0 <= start_a <= 180 and 0 <= end_a <= 180):
        return dbc.Alert("Başlangıç ve Bitiş açıları 0-180 arasında olmalıdır!", color="danger")
    if not (1 <= abs(step_a) <= 45):
        return dbc.Alert("Adım açısı 1-45 arasında olmalıdır!", color="danger")
    if not (0 <= buzzer_d <= 200):
        return dbc.Alert("Buzzer mesafesi 0-200 cm arasında olmalıdır!", color="danger")
    current_pid = None
    if os.path.exists(PID_FILE_PATH_FOR_DASH):
        try:
            with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                pid_str = pf.read().strip()
                if pid_str: current_pid = int(pid_str)
        except (IOError, ValueError):
            current_pid = None
    if current_pid and is_process_running(current_pid):
        return dbc.Alert(f"Betik zaten çalışıyor (PID: {current_pid}).", color="warning")
    if os.path.exists(LOCK_FILE_PATH_FOR_DASH):
        try:
            os.remove(LOCK_FILE_PATH_FOR_DASH)
        except OSError as e:
            return dbc.Alert(f"Kalıntı kilit dosyası silinemedi: {e}.", color="danger")
    if os.path.exists(PID_FILE_PATH_FOR_DASH):
        try:
            os.remove(PID_FILE_PATH_FOR_DASH)
        except OSError as e:
            return dbc.Alert(f"Kalıntı PID dosyası silinemedi: {e}.", color="danger")
    try:
        python_executable = sys.executable
        if not os.path.exists(SENSOR_SCRIPT_PATH):
            return dbc.Alert(f"Sensör betiği bulunamadı: {SENSOR_SCRIPT_PATH}", color="danger")
        cmd = [
            python_executable, SENSOR_SCRIPT_PATH, "--start_angle", str(start_a),
            "--end_angle", str(end_a), "--step_angle", str(step_a), "--buzzer_distance", str(buzzer_d)
        ]
        log_file_path = os.path.join(PROJECT_ROOT_DIR, 'sensor_script.log')
        with open(log_file_path, 'w') as log_file:
            subprocess.Popen(cmd, start_new_session=True, stdout=log_file, stderr=log_file)
        time.sleep(2.5)
        if os.path.exists(PID_FILE_PATH_FOR_DASH):
            new_pid_str = open(PID_FILE_PATH_FOR_DASH).read().strip()
            return dbc.Alert(f"Sensör okumaları başladı (PID: {new_pid_str})...", color="success")
        else:
            log_content = ""
            try:
                with open(log_file_path, 'r') as f:
                    log_content = f.read().strip()
                if log_content:
                    return dbc.Alert([html.B("PID dosyası oluşmadı. Betik Hata Raporu:"), html.Pre(log_content, style={
                        'whiteSpace': 'pre-wrap', 'wordBreak': 'break-all'})], color="danger")
            except Exception:
                pass
            return dbc.Alert(
                f"PID dosyası ({PID_FILE_PATH_FOR_DASH}) oluşmadı. Proje ana dizinindeki 'sensor_script.log' dosyasını kontrol edin.",
                color="danger")
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
                pid_str = pf.read().strip()
                if pid_str: pid_to_kill = int(pid_str)
        except (IOError, ValueError):
            pid_to_kill = None
    if pid_to_kill and is_process_running(pid_to_kill):
        try:
            os.kill(pid_to_kill, signal.SIGTERM)
            time.sleep(2.0)
            if is_process_running(pid_to_kill):
                os.kill(pid_to_kill, signal.SIGKILL)
                time.sleep(0.5)
            if not is_process_running(pid_to_kill):
                return dbc.Alert(f"Betik (PID: {pid_to_kill}) başarıyla durduruldu.", color="info")
            else:
                return dbc.Alert(f"Betik (PID: {pid_to_kill}) durdurulamadı!", color="danger")
        except Exception as e:
            return dbc.Alert(f"Betik (PID: {pid_to_kill}) durdurulurken hata: {e}", color="danger")
    else:
        return dbc.Alert("Çalışan bir sensör betiği bulunamadı.", color="warning")


@app.callback(
    [Output('current-angle', 'children'),
     Output('current-distance', 'children'),
     Output('current-speed', 'children')],
    [Input('interval-component-main', 'n_intervals')]
)
def update_realtime_values(n_intervals):
    conn, error = get_db_connection()
    angle_str, distance_str, speed_str = "--°", "-- cm", "-- cm/s"
    if conn:
        try:
            latest_id = get_latest_scan_id_from_db(conn_param=conn)
            if latest_id:
                df = pd.read_sql_query(
                    f"SELECT angle_deg, mesafe_cm, hiz_cm_s FROM scan_points WHERE scan_id = {latest_id} ORDER BY id DESC LIMIT 1",
                    conn)
                if not df.empty:
                    angle_val, dist_val, speed_val = df.iloc[0]
                    angle_str = f"{angle_val:.0f}°" if pd.notnull(angle_val) else "--°"
                    distance_str = f"{dist_val:.1f} cm" if pd.notnull(dist_val) else "-- cm"
                    speed_str = f"{speed_val:.1f} cm/s" if pd.notnull(speed_val) else "-- cm/s"
        except Exception as e:
            print(f"Anlık değerler güncellenirken hata: {e}")
        finally:
            if conn: conn.close()
    return angle_str, distance_str, speed_str


@app.callback(
    [Output('calculated-area', 'children'),
     Output('perimeter-length', 'children'),
     Output('max-width', 'children'),
     Output('max-depth', 'children')],
    [Input('interval-component-main', 'n_intervals')]
)
def update_analysis_panel(n_intervals):
    conn, error = get_db_connection()
    area_str, perimeter_str, width_str, depth_str = "-- cm²", "-- cm", "-- cm", "-- cm"
    if conn:
        try:
            latest_id = get_latest_scan_id_from_db(conn_param=conn)
            if latest_id:
                df_scan = pd.read_sql_query(
                    f"SELECT hesaplanan_alan_cm2, cevre_cm, max_genislik_cm, max_derinlik_cm FROM servo_scans WHERE id = {latest_id}",
                    conn)
                if not df_scan.empty:
                    scan_data_row = df_scan.iloc[0]
                    area_val = scan_data_row.get('hesaplanan_alan_cm2')
                    per_val = scan_data_row.get('cevre_cm')
                    w_val = scan_data_row.get('max_genislik_cm')
                    d_val = scan_data_row.get('max_derinlik_cm')
                    area_str = f"{area_val:.2f} cm²" if pd.notnull(area_val) else "Hesaplanmadı"
                    perimeter_str = f"{per_val:.2f} cm" if pd.notnull(per_val) else "Hesaplanmadı"
                    width_str = f"{w_val:.2f} cm" if pd.notnull(w_val) else "Hesaplanmadı"
                    depth_str = f"{d_val:.2f} cm" if pd.notnull(d_val) else "Hesaplanmadı"
        except Exception as e:
            print(f"HATA: Analiz paneli DB sorgu hatası: {e}")
        finally:
            if conn: conn.close()
    return area_str, perimeter_str, width_str, depth_str


@app.callback(
    [Output('script-status', 'children'),
     Output('script-status', 'className'),
     Output('cpu-usage', 'value'),
     Output('cpu-usage', 'label'),
     Output('ram-usage', 'value'),
     Output('ram-usage', 'label')],
    [Input('interval-component-system', 'n_intervals')]
)
def update_system_card(n_intervals):
    script_status_text, status_class_name = "Beklemede", "text-secondary"
    pid = None
    if os.path.exists(PID_FILE_PATH_FOR_DASH):
        try:
            with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                pid_str = pf.read().strip()
                if pid_str: pid = int(pid_str)
        except (IOError, ValueError):
            pass
    if pid and is_process_running(pid):
        script_status_text, status_class_name = f"Çalışıyor (PID: {pid})", "text-success"
    else:
        script_status_text, status_class_name = "Çalışmıyor", "text-danger"
    cpu_percent = psutil.cpu_percent(interval=0.1)
    ram_percent = psutil.virtual_memory().percent
    return script_status_text, status_class_name, cpu_percent, f"{cpu_percent}%", ram_percent, f"{ram_percent}%"


@app.callback(
    [Output('scan-map-graph', 'figure'),
     Output('polar-graph', 'figure'),
     Output('time-series-graph', 'figure'),
     Output('environment-estimation-text', 'children')],
    [Input('interval-component-main', 'n_intervals')]
)
def update_all_graphs(n_intervals):
    conn, error_msg_conn = get_db_connection()
    id_to_plot = get_latest_scan_id_from_db(conn_param=conn) if conn else None
    fig_map = go.Figure()
    fig_polar = go.Figure(layout=go.Layout(title='Polar Grafik', uirevision=id_to_plot))
    fig_time = go.Figure(layout=go.Layout(title='Zaman Serisi - Mesafe', uirevision=id_to_plot))
    estimation_text = "Veri bekleniyor..."
    if not conn or not id_to_plot:
        if conn: conn.close()
        fig_map.update_layout(title_text='Ortamın 2D Haritası (2D Map of the Environment)',
                              xaxis_title="X Mesafesi (cm)", yaxis_title="Y Mesafesi (cm)")
        return fig_map, fig_polar, fig_time, "Tarama başlatın."
    try:
        df_points = pd.read_sql_query(
            f"SELECT x_cm, y_cm, angle_deg, mesafe_cm, timestamp FROM scan_points WHERE scan_id = {id_to_plot} ORDER BY angle_deg ASC",
            conn)
        if not df_points.empty:
            df_valid = df_points[(df_points['mesafe_cm'] > 1.0) & (df_points['mesafe_cm'] < 250.0)].copy()
            if len(df_valid) >= 2:
                add_scan_rays(fig_map, df_valid)
                add_sector_area(fig_map, df_valid)
                estimation_text = analyze_environment_shape(fig_map, df_valid)
                add_detected_points(fig_map, df_valid)
                add_sensor_position(fig_map)
                update_polar_graph(fig_polar, df_valid)
                update_time_series_graph(fig_time, df_valid)
            else:
                estimation_text = "Grafik için yeterli geçerli nokta bulunamadı."
    except Exception as e:
        print(f"HATA: Grafikleme hatası: {e}")
        estimation_text = f"Hata: {e}"
    finally:
        if conn: conn.close()
    fig_map.update_layout(
        title_text='Ortamın 2D Haritası (2D Map of the Environment)',
        xaxis_title="X Mesafesi (cm)", yaxis_title="Y Mesafesi (cm)",
        yaxis_scaleanchor="x", yaxis_scaleratio=1,
        legend_title_text='Gösterim Katmanları', uirevision=id_to_plot
    )
    return fig_map, fig_polar, fig_time, estimation_text


@app.callback(
    Output('download-csv', 'data'),
    [Input('export-csv-button', 'n_clicks')],
    prevent_initial_call=True)
def export_csv_callback(n_clicks):
    if n_clicks == 0: return no_update
    conn, _ = get_db_connection()
    if not conn: return no_update
    try:
        latest_id = get_latest_scan_id_from_db(conn_param=conn)
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
        latest_id = get_latest_scan_id_from_db(conn_param=conn)
        if latest_id:
            df_points = pd.read_sql_query(f"SELECT * FROM scan_points WHERE scan_id = {latest_id} ORDER BY id ASC",
                                          conn)
            df_scan_info = pd.read_sql_query(f"SELECT * FROM servo_scans WHERE id = {latest_id}", conn)
            excel_buffer = io.BytesIO()
            with pd.ExcelWriter(excel_buffer, engine='xlsxwriter') as writer:
                df_points.to_excel(writer, sheet_name=f'Scan_{latest_id}_Points', index=False)
                df_scan_info.to_excel(writer, sheet_name=f'Scan_{latest_id}_Info', index=False)
            excel_buffer.seek(0)
            return dcc.send_bytes(excel_buffer.read(), f"tarama_detaylari_id_{latest_id}.xlsx")
    finally:
        if conn: conn.close()
    return no_update


@app.callback(
    Output('tab-content-datatable', 'children'),
    [Input('visualization-tabs-main', 'active_tab'),
     Input('interval-component-main', 'n_intervals')]
)
def render_and_update_data_table(active_tab, n_intervals):
    if active_tab == "tab-datatable":
        conn, error = get_db_connection()
        if not conn:
            return dbc.Alert("Veritabanı bağlantısı kurulamadı.", color="danger")
        try:
            latest_id = get_latest_scan_id_from_db(conn_param=conn)
            if not latest_id:
                return html.P("Henüz görüntülenecek tarama verisi yok.")
            query = f"SELECT id, angle_deg, mesafe_cm, hiz_cm_s, x_cm, y_cm, timestamp FROM scan_points WHERE scan_id = {latest_id} ORDER BY id DESC"
            df = pd.read_sql_query(query, conn)
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s').dt.strftime('%H:%M:%S.%f').str[:-3]
            columns = [{"name": i.replace("_", " ").title(), "id": i} for i in df.columns]
            data = df.to_dict('records')
            return dash_table.DataTable(
                id='scan-data-table',
                data=data,
                columns=columns,
                style_cell={'textAlign': 'left', 'padding': '5px'},
                style_header={'backgroundColor': 'rgb(230, 230, 230)', 'fontWeight': 'bold'},
                style_table={'height': '70vh', 'overflowY': 'auto'},
                page_size=20,
                sort_action="native",
                filter_action="native",
            )
        except Exception as e:
            print(f"Veri tablosu oluşturulurken hata: {e}")
            return dbc.Alert(f"Tablo oluşturulurken hata oluştu: {e}", color="danger")
        finally:
            if conn: conn.close()
    return None