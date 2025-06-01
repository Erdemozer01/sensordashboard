# dashboard_app/dash_apps.py (Final Düzenlenmiş Hali)

import os
import sys

import scipy

print(f"--- [PID:{os.getpid()}] Executing dash_apps.py: Top of file ---")

try:
    from django_plotly_dash import DjangoDash

    print(f"--- [PID:{os.getpid()}] dash_apps.py: DjangoDash imported successfully ---")
except ImportError as e_dpd:
    print(f"!!!!!! [PID:{os.getpid()}] dash_apps.py: FAILED to import DjangoDash: {e_dpd} !!!!!!")
    raise
except Exception as e_dpd_other:
    print(f"!!!!!! [PID:{os.getpid()}] dash_apps.py: UNEXPECTED ERROR importing DjangoDash: {e_dpd_other} !!!!!!")
    raise

try:
    import dash
    from dash import html, dcc, Output, Input, State, no_update, dash_table
    import dash_bootstrap_components as dbc
    import plotly.graph_objects as go

    print(f"--- [PID:{os.getpid()}] dash_apps.py: Dash and Plotly components imported successfully ---")
except ImportError as e_dash_core:
    print(
        f"!!!!!! [PID:{os.getpid()}] dash_apps.py: FAILED to import core Dash/Plotly components: {e_dash_core} !!!!!!")
    raise
except Exception as e_dash_core_other:
    print(
        f"!!!!!! [PID:{os.getpid()}] dash_apps.py: UNEXPECTED ERROR importing core Dash/Plotly components: {e_dash_core_other} !!!!!!")
    raise

import sqlite3
import pandas as pd
import subprocess
import time
import io
import signal
import psutil
import numpy as np
import math  # math importu eksikti, eklendi (sensor_script'te vardı, burada da gerekebilir)

scipy_available = False
simplification_available = False

try:
    from scipy.spatial import ConvexHull

    scipy_available = True
    print(f"--- [PID:{os.getpid()}] dash_apps.py: scipy.spatial imported successfully ---")
except ImportError:
    print(f"--- [PID:{os.getpid()}] dash_apps.py: scipy.spatial not found, ConvexHull will be disabled. ---")
try:
    from simplification.cutil import simplify_coords

    simplification_available = True
    print(f"--- [PID:{os.getpid()}] dash_apps.py: simplification.cutil imported successfully ---")
except ImportError:
    print(f"--- [PID:{os.getpid()}] dash_apps.py: simplification.cutil not found, simplification will be disabled. ---")

print(f"--- [PID:{os.getpid()}] dash_apps.py: All other standard imports attempted ---")

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

# UI için varsayılanlar (kullanıcının son kodundan alındı)
DEFAULT_UI_SCAN_START_ANGLE = 0
DEFAULT_UI_SCAN_END_ANGLE = 180  # Bu, sensor_script'e gönderilecek doğrudan bitiş açısı olacak
DEFAULT_UI_SCAN_STEP_ANGLE = 10

print(
    f"--- [PID:{os.getpid()}] dash_apps.py: Constants defined. DB_PATH: {DB_PATH}, SENSOR_SCRIPT_PATH: {SENSOR_SCRIPT_PATH} ---")
print(f"--- [PID:{os.getpid()}] dash_apps.py: About to define 'app' with DjangoDash for RealtimeSensorDashboard ---")

app = None
try:
    stylesheets = [dbc.themes.BOOTSTRAP]
    if not all(isinstance(s, str) for s in stylesheets if s):
        print(f"!!!!!! [PID:{os.getpid()}] dash_apps.py: Invalid external_stylesheets: {stylesheets} !!!!!!")
        raise ValueError("external_stylesheets must be a list of strings.")
    app = DjangoDash('RealtimeSensorDashboard', external_stylesheets=stylesheets)
    print(
        f"--- [PID:{os.getpid()}] dash_apps.py: 'app' (DjangoDash instance) DEFINED SUCCESSFULLY! Type: {type(app)} ---")
except Exception as e_django_dash:
    print(f"!!!!!! [PID:{os.getpid()}] dash_apps.py: ERROR defining 'app' with DjangoDash: {e_django_dash} !!!!!!")
    import traceback

    traceback.print_exc()

if app is None:
    print(
        f"!!!!!! [PID:{os.getpid()}] dash_apps.py: CRITICAL - 'app' could not be initialized. DjangoDash instantiation failed. !!!!!!")

# --- LAYOUT BİLEŞENLERİ ---
title_card = dbc.Row([dbc.Col(html.H1("Dream Pi", className="text-center my-3"), width=12), html.Hr(), ])
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
        dbc.InputGroup([dbc.InputGroupText("Başl. Açı (°)", style={"width": "120px"}),  # UI'da 0-180 arası
                        dbc.Input(id="start-angle-input", type="number", value=DEFAULT_UI_SCAN_START_ANGLE, min=-179,
                                  # sensor_script +/- kabul ediyor
                                  max=179, step=5)], className="mb-2"),
        dbc.InputGroup([dbc.InputGroupText("Bitiş Açısı (°)", style={"width": "120px"}),  # UI'da 0-180 arası
                        dbc.Input(id="end-angle-input", type="number", value=DEFAULT_UI_SCAN_END_ANGLE, min=-179,
                                  # sensor_script +/- kabul ediyor
                                  max=179, step=5)], className="mb-2"),
        dbc.InputGroup([dbc.InputGroupText("Adım Açısı (°)", style={"width": "120px"}),
                        dbc.Input(id="step-angle-input", type="number", value=DEFAULT_UI_SCAN_STEP_ANGLE, min=1, max=45,
                                  step=1)], className="mb-2"),
    ])
])
stats_panel = dbc.Card([dbc.CardHeader("Anlık Sensör Değerleri", className="bg-info text-white"), dbc.CardBody(dbc.Row(
    [dbc.Col(html.Div([html.H6("Mevcut Açı:"), html.H4(id='current-angle', children="--°")]), width=4,
             className="text-center"),
     dbc.Col(html.Div([html.H6("Mevcut Mesafe:"), html.H4(id='current-distance', children="-- cm")]), width=4,
             className="text-center"),
     dbc.Col(html.Div([html.H6("Anlık Hız:"), html.H4(id='current-speed', children="-- cm/s")]), width=4,
             className="text-center")]))], className="mb-3")
system_card = dbc.Card([dbc.CardHeader("Sistem Durumu", className="bg-secondary text-white"), dbc.CardBody(
    [dbc.Row([dbc.Col(html.Div([html.H6("Sensör Durumu:"), html.H5(id='script-status', children="Beklemede")]))],
             className="mb-2"), dbc.Row([dbc.Col(html.Div([html.H6("Pi CPU Kullanımı:"),
                                                           dbc.Progress(id='cpu-usage', value=0, color="success",
                                                                        style={"height": "20px"}, className="mb-1",
                                                                        label="0%")])), dbc.Col(html.Div(
        [html.H6("Pi RAM Kullanımı:"),
         dbc.Progress(id='ram-usage', value=0, color="info", style={"height": "20px"}, className="mb-1",
                      label="0%")]))])])], className="mb-3")
export_card = dbc.Card([dbc.CardHeader("Veri Dışa Aktarma (En Son Tarama)", className="bg-light"), dbc.CardBody(
    [dbc.Button('En Son Taramayı CSV İndir', id='export-csv-button', color="primary", className="w-100 mb-2"),
     dcc.Download(id='download-csv'),
     dbc.Button('En Son Taramayı Excel İndir', id='export-excel-button', color="success", className="w-100"),
     dcc.Download(id='download-excel'), ])], className="mb-3")
analysis_card = dbc.Card([dbc.CardHeader("Tarama Analizi (En Son Tarama)", className="bg-dark text-white"),
                          dbc.CardBody([dbc.Row(
                              [dbc.Col([html.H6("Hesaplanan Alan:"), html.H4(id='calculated-area', children="-- cm²")]),
                               dbc.Col(
                                   [html.H6("Çevre Uzunluğu:"), html.H4(id='perimeter-length', children="-- cm")])]),
                                        dbc.Row([dbc.Col(
                                            [html.H6("Max Genişlik:"), html.H4(id='max-width', children="-- cm")]),
                                                 dbc.Col([html.H6("Max Derinlik:"),
                                                          html.H4(id='max-depth', children="-- cm")])],
                                                className="mt-2")])])
estimation_card = dbc.Card([dbc.CardHeader("Ortam Şekli Tahmini", className="bg-success text-white"), dbc.CardBody(
    html.H4("Tahmin: Bekleniyor...", id='environment-estimation-text', className="text-center"))])
visualization_tabs = dbc.Tabs(
    [dbc.Tab(dcc.Graph(id='scan-map-graph', style={'height': '75vh'}), label="2D Kartezyen Harita"),
     dbc.Tab(dcc.Graph(id='polar-graph', style={'height': '75vh'}), label="Polar Grafik"),
     dbc.Tab(dcc.Graph(id='time-series-graph', style={'height': '75vh'}), label="Zaman Serisi (Mesafe)"), dbc.Tab(
        dcc.Loading(children=[dash_table.DataTable(id='scan-data-table', columns=[], data=[], page_size=20,
                                                   style_cell={'textAlign': 'left', 'padding': '5px'},
                                                   style_header={'backgroundColor': 'rgb(230, 230, 230)',
                                                                 'fontWeight': 'bold'},
                                                   style_table={'height': '70vh', 'overflowY': 'auto'},
                                                   sort_action="native", filter_action="native", )]),
        label="Veri Tablosu")])

if app:
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
        dcc.Interval(id='interval-component-main', interval=1500, n_intervals=0),  # Interval'ı 1500ms'ye düşürdüm
        dcc.Interval(id='interval-component-system', interval=3000, n_intervals=0),
        dcc.Store(id='live-scan-data-store')  # Merkezi veri deposu
    ])
else:
    print(f"!!!!!! [PID:{os.getpid()}] dash_apps.py: 'app' is None, so app.layout is NOT being assigned. !!!!!!")


# --- HELPER FONKSİYONLAR ---
def is_process_running(pid):
    if pid is None: return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    except Exception:
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
    internal_conn = False;
    conn_to_use = conn_param;
    latest_id = None
    if not conn_to_use:
        conn_to_use, error = get_db_connection()
        if error: print(f"DB Hatası (get_latest_scan_id): {error}"); return None
        internal_conn = True
    if conn_to_use:
        try:
            query = "SELECT id FROM servo_scans ORDER BY CASE status WHEN 'running' THEN 1 WHEN 'completed_analysis' THEN 2 WHEN 'completed' THEN 3 ELSE 4 END, start_time DESC LIMIT 1"
            df_scan = pd.read_sql_query(query, conn_to_use)
            if not df_scan.empty: latest_id = int(df_scan['id'].iloc[0])
        except (sqlite3.Error, pd.io.sql.DatabaseError) as e:
            print(f"Son tarama ID DB hatası: {e}"); latest_id = None
        except Exception as e:
            print(f"Son tarama ID genel hata: {e}"); latest_id = None
        finally:
            if internal_conn and conn_to_use:
                try:
                    conn_to_use.close()
                except sqlite3.Error as e:
                    print(f"DB kapatma hatası (get_latest_scan_id): {e}")
    return latest_id


# --- CALLBACK FONKSİYONLARI ---
if app:  # Sadece app başarılı ise callback'leri tanımla
    @app.callback(Output('live-scan-data-store', 'data'), Input('interval-component-main', 'n_intervals'))
    def fetch_data_for_store(n_intervals):
        conn, db_conn_error = get_db_connection()
        output_data = {'points': [], 'info': {}, 'scan_id': None, 'error': None}
        if db_conn_error or not conn:
            output_data['error'] = db_conn_error or "Veritabanı bağlantısı kurulamadı."
            return output_data
        latest_id = get_latest_scan_id_from_db(conn_param=conn)
        output_data['scan_id'] = latest_id
        if not latest_id:
            if conn: conn.close()
            output_data['error'] = 'Gösterilecek tarama ID bulunamadı.'
            return output_data
        try:
            df_scan_info = pd.read_sql_query(f"SELECT * FROM servo_scans WHERE id = {latest_id}", conn)
            if not df_scan_info.empty:
                output_data['info'] = df_scan_info.to_dict('records')[0]
            else:
                output_data['error'] = f"Tarama ID {latest_id} için bilgi bulunamadı."
            df_points = pd.read_sql_query(
                f"SELECT id, angle_deg, mesafe_cm, hiz_cm_s, timestamp, x_cm, y_cm FROM scan_points WHERE scan_id = {latest_id} ORDER BY id ASC",
                conn)
            output_data['points'] = df_points.to_dict('records')
            return output_data
        except (sqlite3.Error, pd.io.sql.DatabaseError) as e:
            output_data['error'] = f"Veri çekme DB hatası: {str(e)[:100]}"; return output_data
        except Exception as e:
            output_data['error'] = f"Genel veri çekme hatası: {str(e)[:100]}"; return output_data
        finally:
            if conn:
                try:
                    conn.close()
                except sqlite3.Error as e_close:
                    print(f"DB kapatma hatası (fetch_data_for_store): {e_close}")


    @app.callback(Output('scan-status-message', 'children'),
                  [Input('start-scan-button', 'n_clicks')],
                  [State('start-angle-input', 'value'), State('end-angle-input', 'value'),
                   State('step-angle-input', 'value')],
                  prevent_initial_call=True)
    def handle_start_scan_script(n_clicks_start, start_angle_val, end_angle_val, step_angle_val):
        if n_clicks_start == 0: return no_update

        # UI'dan gelen start_a ve end_a, sensor_script'e initial_goto_angle ve scan_end_angle olarak gidecek.
        # sensor_script_final bu iki mutlak açı arasındaki farktan tarama yayını ve yönünü kendi hesaplar.
        initial_a = start_angle_val if start_angle_val is not None else DEFAULT_UI_SCAN_START_ANGLE
        final_a = end_angle_val if end_angle_val is not None else DEFAULT_UI_SCAN_END_ANGLE
        step_a = step_angle_val if step_angle_val is not None else DEFAULT_UI_SCAN_STEP_ANGLE

        # Parametre doğrulama (UI'daki min/max'a ek olarak)
        if not (-179 <= initial_a <= 179): return dbc.Alert("Başlangıç açısı -179 ile 179 arasında olmalı!",
                                                            color="danger", duration=4000)
        if not (-179 <= final_a <= 179): return dbc.Alert("Bitiş açısı -179 ile 179 arasında olmalı!", color="danger",
                                                          duration=4000)
        if initial_a == final_a: return dbc.Alert("Başlangıç ve bitiş açıları farklı olmalı!", color="danger",
                                                  duration=4000)
        if not (1 <= step_a <= 45): return dbc.Alert("Adım açısı 1 ile 45 arasında olmalı!", color="danger",
                                                     duration=4000)

        current_pid = None  # ... (PID ve kilit dosyası kontrolü önceki gibi)
        if os.path.exists(PID_FILE_PATH_FOR_DASH):
            try:
                with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                    pid_str = pf.read().strip();
                if pid_str: current_pid = int(pid_str)
            except (IOError, ValueError):
                current_pid = None
        if current_pid and is_process_running(current_pid): return dbc.Alert(
            f"Betik zaten çalışıyor (PID: {current_pid}).", color="warning", duration=4000)
        if os.path.exists(LOCK_FILE_PATH_FOR_DASH):
            try:
                os.remove(LOCK_FILE_PATH_FOR_DASH);
                if os.path.exists(PID_FILE_PATH_FOR_DASH): os.remove(PID_FILE_PATH_FOR_DASH)
            except OSError as e:
                return dbc.Alert(f"Kalıntı kilit/PID silinemedi: {e}.", color="danger", duration=4000)
        try:
            python_executable = sys.executable
            if not os.path.exists(SENSOR_SCRIPT_PATH): return dbc.Alert(f"Betik bulunamadı: {SENSOR_SCRIPT_PATH}",
                                                                        color="danger", duration=4000)

            cmd = [python_executable, SENSOR_SCRIPT_PATH,
                   "--initial_goto_angle", str(initial_a),  # sensor_script_final bu argümanları bekliyor
                   "--scan_end_angle", str(final_a),
                   "--scan_step_angle", str(step_a)]
            print(f"Dash: Sensör betiği başlatılıyor: {' '.join(cmd)}")
            process = subprocess.Popen(cmd, start_new_session=(os.name == 'posix'),
                                       creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name != 'posix' else 0),
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            time.sleep(2.5);
            stdout_data, stderr_data = "", ""
            try:
                if process.poll() is not None:
                    stdout_data_bytes, stderr_data_bytes = process.communicate(timeout=1);
                    stdout_data = stdout_data_bytes.decode(errors='ignore');
                    stderr_data = stderr_data_bytes.decode(errors='ignore')
                if stderr_data: print(f"Sensor Script STDERR: {stderr_data}")
                if stdout_data: print(f"Sensor Script STDOUT: {stdout_data}")
            except subprocess.TimeoutExpired:
                pass
            except Exception as e_comm:
                print(f"Sensor script communicate hatası: {e_comm}")

            if os.path.exists(PID_FILE_PATH_FOR_DASH):
                new_pid = None
                try:
                    with open(PID_FILE_PATH_FOR_DASH, 'r') as pf_new:
                        pid_str_new = pf_new.read().strip()
                    if pid_str_new: new_pid = int(pid_str_new)
                    if new_pid and is_process_running(new_pid):
                        return dbc.Alert(f"Sensör okumaları başladı (PID: {new_pid}).", color="success", duration=4000)
                    else:
                        err_msg = f"Sensör okumaları başlatıldı ancak process (PID: {new_pid}) bulunamadı.";
                        if stderr_data: err_msg += f" Betik Hatası: {stderr_data[:100]}"
                        return dbc.Alert(err_msg, color="warning", duration=6000)
                except Exception as e:
                    return dbc.Alert(f"PID dosyası okunurken hata: {e}", color="warning", duration=4000)
            else:
                err_msg = f"PID dosyası ({PID_FILE_PATH_FOR_DASH}) oluşmadı."
                if process.poll() is not None: err_msg += f" Betik sonlandı (kod: {process.poll()})."
                if stderr_data: err_msg += f" Betik Hatası: {stderr_data[:100]}"
                return dbc.Alert(err_msg, color="danger", duration=6000)
        except Exception as e:
            return dbc.Alert(f"Sensör başlatılırken hata: {str(e)}", color="danger", duration=6000)


    # Diğer callback'ler (update_all_graphs, update_realtime_values, update_analysis_panel, vb.)
    # `Input('live-scan-data-store', 'data')` kullanacak şekilde güncellenmeli.
    # Bu, `dashboard_app_v4 (Try-Except Gözden Geçirilmiş)` Canvas'ındaki gibidir.
    # Örnek olarak update_all_graphs ve update_analysis_panel'i tekrar ekliyorum.
    # Diğerlerini de benzer şekilde dcc.Store'dan okuyacak şekilde düzenlemeniz gerekir.

    @app.callback(
        [Output('scan-map-graph', 'figure'), Output('polar-graph', 'figure'), Output('time-series-graph', 'figure')],
        Input('live-scan-data-store', 'data')
    )
    def update_all_graphs_from_store(stored_data):  # Callback adı değişebilir, ama Output aynı kalmalı
        # ... (dashboard_app_v4'teki update_all_graphs içeriği buraya gelecek)
        try:
            empty_fig_layout = go.Layout(title_text='Veri Bekleniyor...', paper_bgcolor='rgba(0,0,0,0)',
                                         plot_bgcolor='rgba(248,248,248,0.95)');
            error_fig_layout = go.Layout(title_text='Grafik Yüklenemedi', paper_bgcolor='rgba(0,0,0,0)',
                                         plot_bgcolor='rgba(248,248,248,0.95)');
            empty_fig = go.Figure(layout=empty_fig_layout);
            error_fig = go.Figure(layout=error_fig_layout)
            if not stored_data: return empty_fig, empty_fig, empty_fig
            db_error = stored_data.get('error');
            scan_id_for_title = stored_data.get('scan_id', "N/A");
            scan_status_for_title = stored_data.get('info', {}).get('status', 'Bilinmiyor');
            title_suffix_base = f"(ID: {scan_id_for_title}, Durum: {scan_status_for_title})"
            if db_error: error_fig.update_layout(
                title_text=f'Veri Hatası: {str(db_error)[:50]} {title_suffix_base}'); return error_fig, error_fig, error_fig
            if not stored_data.get('points') and not db_error: empty_fig.update_layout(
                title_text=f'Nokta Verisi Yok {title_suffix_base}'); return empty_fig, empty_fig, empty_fig
            df_points = pd.DataFrame(stored_data.get('points', []));
            title_suffix = title_suffix_base;
            max_plot_dist = 200.0
            fig_map_layout = go.Layout(title_text='2D Harita ' + title_suffix, xaxis_title="Yatay (cm)",
                                       yaxis_title="İleri (cm)", yaxis_scaleanchor="x", yaxis_scaleratio=1,
                                       paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(248,248,248,0.95)');
            fig_map = go.Figure(layout=fig_map_layout)
            if not df_points.empty:
                try:
                    df_valid_map = df_points[
                        (df_points['mesafe_cm'] > 0.1) & (df_points['mesafe_cm'] < max_plot_dist) & df_points[
                            'x_cm'].notna() & df_points['y_cm'].notna()].copy()
                    if not df_valid_map.empty: fig_map.add_trace(
                        go.Scatter(x=df_valid_map['y_cm'], y=df_valid_map['x_cm'], mode='lines+markers', name='Sınır',
                                   marker=dict(size=5, color=df_valid_map['mesafe_cm'], colorscale='Viridis',
                                               showscale=False),
                                   line=dict(color='dodgerblue'))); polygon_plot_x = [0.0] + list(
                        df_valid_map['y_cm']) + [0.0]; polygon_plot_y = [0.0] + list(df_valid_map['x_cm']) + [
                        0.0]; fig_map.add_trace(
                        go.Scatter(x=polygon_plot_x, y=polygon_plot_y, fill="toself", fillcolor='rgba(0,176,246,0.2)',
                                   line=dict(color='rgba(255,255,255,0)'), showlegend=False))
                except KeyError as ke:
                    fig_map.update_layout(title_text='Harita Veri Formatı Hatası')
                fig_map.add_trace(
                    go.Scatter(x=[0], y=[0], mode='markers', marker=dict(size=10, symbol='diamond', color='red'),
                               name='Sensör'))
            fig_polar_layout = go.Layout(title_text='Polar Grafik ' + title_suffix, paper_bgcolor='rgba(0,0,0,0)',
                                         plot_bgcolor='rgba(248,248,248,0.95)');
            fig_polar = go.Figure(layout=fig_polar_layout)
            if not df_points.empty:
                try:
                    df_valid_polar = df_points[
                        (df_points['mesafe_cm'] > 0.1) & (df_points['mesafe_cm'] < max_plot_dist) & df_points[
                            'angle_deg'].notna()].copy()
                    if not df_valid_polar.empty: fig_polar.add_trace(
                        go.Scatterpolar(r=df_valid_polar['mesafe_cm'], theta=df_valid_polar['angle_deg'],
                                        mode='lines+markers', name='Mesafe',
                                        marker=dict(color=df_valid_polar['mesafe_cm'], colorscale='Viridis',
                                                    showscale=True, colorbar_title_text="Mesafe(cm)")))
                except KeyError as ke:
                    fig_polar.update_layout(title_text='Polar Veri Formatı Hatası')
            fig_polar.update_layout(polar=dict(radialaxis=dict(visible=True, range=[0, max_plot_dist]),
                                               angularaxis=dict(direction="counterclockwise", ticksuffix="°")))
            fig_time_layout = go.Layout(title_text='Zaman Serisi - Mesafe ' + title_suffix, xaxis_title="Zaman",
                                        yaxis_title="Mesafe (cm)", paper_bgcolor='rgba(0,0,0,0)',
                                        plot_bgcolor='rgba(248,248,248,0.95)');
            fig_time = go.Figure(layout=fig_time_layout)
            if not df_points.empty and 'timestamp' in df_points.columns:
                try:
                    df_valid_time = df_points[
                        (df_points['mesafe_cm'] > 0.1) & (df_points['mesafe_cm'] < max_plot_dist) & df_points[
                            'timestamp'].notna()].copy()
                    if not df_valid_time.empty: df_valid_time_sorted = df_valid_time.sort_values(
                        by='timestamp'); datetime_series = pd.to_datetime(df_valid_time_sorted['timestamp'],
                                                                          unit='s'); fig_time.add_trace(
                        go.Scatter(x=datetime_series, y=df_valid_time_sorted['mesafe_cm'], mode='lines+markers',
                                   name='Mesafe (cm)')); fig_time.update_xaxes(type='date', tickformat='%H:%M:%S')
                except KeyError as ke:
                    fig_time.update_layout(title_text='Zaman Serisi Veri Formatı Hatası')
            return fig_map, fig_polar, fig_time
        except Exception as e:
            print(f"Grafik oluşturma sırasında genel hata: {e}"); error_fig.update_layout(
                title_text=f'Grafik Oluşturma Hatası: {str(e)[:30]}'); return error_fig, error_fig, error_fig


    @app.callback(
        [Output('current-angle', 'children'), Output('current-distance', 'children'),
         Output('current-speed', 'children')],
        Input('live-scan-data-store', 'data')
    )
    def update_realtime_values_from_store(stored_data):
        # ... (dashboard_app_v4'teki update_realtime_values içeriği buraya gelecek)
        angle_str, distance_str, speed_str = "--°", "-- cm", "-- cm/s"
        try:
            if stored_data and stored_data.get('points') and len(stored_data['points']) > 0:
                last_point = stored_data['points'][-1];
                angle_val = last_point.get('angle_deg');
                dist_val = last_point.get('mesafe_cm');
                speed_val = last_point.get('hiz_cm_s')
                angle_str = f"{angle_val:.0f}°" if pd.notnull(angle_val) else "--°";
                distance_str = f"{dist_val:.1f} cm" if pd.notnull(dist_val) else "-- cm";
                speed_str = f"{speed_val:.1f} cm/s" if pd.notnull(speed_val) else "-- cm/s"
        except IndexError:
            print("update_realtime_values: Boş 'points' listesi.")
        except KeyError as ke:
            print(f"update_realtime_values: Anahtar hatası: {ke}")
        except Exception as e:
            print(f"Anlık değerler güncellenirken genel hata: {e}")
        return angle_str, distance_str, speed_str


    @app.callback(
        [Output('calculated-area', 'children'), Output('perimeter-length', 'children'), Output('max-width', 'children'),
         Output('max-depth', 'children')],
        Input('live-scan-data-store', 'data')
    )
    def update_analysis_panel_from_store(stored_data):
        # ... (dashboard_app_v4'teki update_analysis_panel_numeric içeriği buraya gelecek)
        area_str, perimeter_str, width_str, depth_str = "-- cm²", "-- cm", "-- cm", "-- cm"
        if not stored_data: return area_str, perimeter_str, width_str, depth_str
        info = stored_data.get('info');
        scan_status = info.get('status') if isinstance(info, dict) else None
        if isinstance(info, dict) and info:
            if scan_status == 'running':
                area_str, perimeter_str, width_str, depth_str = "Tarama Sürüyor", "Tarama Sürüyor", "Tarama Sürüyor", "Tarama Sürüyor"
            elif scan_status == 'completed_analysis':
                area_val = info.get('hesaplanan_alan_cm2');
                perimeter_val = info.get('cevre_cm');
                width_val = info.get('max_genislik_cm');
                depth_val = info.get('max_derinlik_cm')
                area_str = f"{area_val:.2f} cm²" if pd.notnull(area_val) else "Hesaplanmadı";
                perimeter_str = f"{perimeter_val:.2f} cm" if pd.notnull(perimeter_val) else "Hesaplanmadı";
                width_str = f"{width_val:.2f} cm" if pd.notnull(width_val) else "Hesaplanmadı";
                depth_str = f"{depth_val:.2f} cm" if pd.notnull(depth_val) else "Hesaplanmadı"
            elif scan_status in ['completed_insufficient_points', 'completed_analysis_error', 'completed']:
                area_str, perimeter_str, width_str, depth_str = "Hesaplanmadı", "Hesaplanmadı", "Hesaplanmadı", "Hesaplanmadı"
            elif not scan_status and stored_data.get('scan_id'):
                area_str, perimeter_str, width_str, depth_str = "Durum Belirsiz", "Durum Belirsiz", "Durum Belirsiz", "Durum Belirsiz"
        elif stored_data.get('error'):
            error_msg_short = str(stored_data.get('error'))[
                              :20]; area_str, perimeter_str, width_str, depth_str = f"Hata: {error_msg_short}", "Hata", "Hata", "Hata"
        return area_str, perimeter_str, width_str, depth_str


    @app.callback(
        [Output('scan-data-table', 'data'), Output('scan-data-table', 'columns')],
        Input('live-scan-data-store', 'data')
    )
    def update_data_table_from_store(stored_data):
        # ... (dashboard_app_v4'teki update_data_table içeriği buraya gelecek)
        if not stored_data or not stored_data.get('points'): return [], [
            {"name": "Veri Yok / Hata", "id": "placeholder"}]
        try:
            df_table = pd.DataFrame(stored_data['points'])
            if df_table.empty: return [], [{"name": "Tarama Noktası Yok", "id": "no_points"}]
            if 'timestamp' in df_table.columns:
                df_table['timestamp_str'] = pd.to_datetime(df_table['timestamp'], unit='s',
                                                           errors='coerce').dt.strftime('%H:%M:%S.%f').str[:-3]
            else:
                df_table['timestamp_str'] = 'N/A'
            numeric_cols = ['mesafe_cm', 'hiz_cm_s', 'x_cm', 'y_cm', 'angle_deg'];
            for col in numeric_cols:
                if col in df_table.columns: df_table[col] = pd.to_numeric(df_table[col], errors='coerce').round(2)
            display_columns = ['id', 'angle_deg', 'mesafe_cm', 'hiz_cm_s', 'timestamp_str', 'x_cm', 'y_cm']
            final_df_table = df_table[[col for col in display_columns if col in df_table.columns]]
            table_data = final_df_table.to_dict('records')
            table_columns = [{"name": col.replace("_", " ").title().replace('Timestamp Str', 'Zaman'), "id": col} for
                             col in final_df_table.columns]
            return table_data, table_columns
        except Exception as e:
            print(f"Veri tablosu oluşturulurken hata: {e}"); return [], [
                {"name": f"Tablo Hatası: {str(e)[:30]}", "id": "table_error"}]


    @app.callback(
        Output('environment-estimation-text', 'children'),
        [Input('interval-component-main', 'n_intervals')],  # Bu da periyodik olarak güncellenebilir
        State('live-scan-data-store', 'data'),  # Veriyi store'dan alacak
        prevent_initial_call=True  # İlk yüklemede çalışmasın
    )
    def update_environment_estimation(n_intervals, stored_data):
        # ... (Kullanıcının update_all_graphs içindeki estimation logic'i buraya taşınacak ve stored_data kullanacak)
        # ... Bu callback, 'estimate-shape-button' yerine interval ile tetiklenecek şekilde değiştirildi.
        # ... Eğer butonla tetiklenmesi isteniyorsa, Input('estimate-shape-button', 'n_clicks') olmalı.
        # ... Şimdilik interval ile güncelleniyor.
        default_text = "Tahmin: Bekleniyor..."
        if not stored_data: return default_text
        scan_info = stored_data.get('info');
        points_data = stored_data.get('points');
        scan_status = scan_info.get('status') if isinstance(scan_info, dict) else None
        if not scan_info or not isinstance(scan_info, dict) or not scan_info:
            if scan_status == 'running': return "Tarama sürüyor..."
            return "Tahmin için tarama bilgisi eksik."
        if scan_status == 'running': return "Tarama sürüyor..."
        if scan_status not in ['completed_analysis']: return f"Analiz bekleniyor (Durum: {scan_status})."

        shape_guess = default_text
        try:
            max_g = scan_info.get('max_genislik_cm');
            max_d = scan_info.get('max_derinlik_cm')
            if pd.notnull(max_g) and pd.notnull(max_d) and max_d > 0.01:
                aspect_ratio = max_g / max_d
                if 0.8 < aspect_ratio < 1.25:
                    shape_guess = "Oda benzeri/Geniş alan."
                elif aspect_ratio >= 1.25:
                    shape_guess = "Geniş koridor/Yayvan engel."
                else:
                    shape_guess = "Dar koridor/Uzun engel."
                shape_guess += f" (G/D: {aspect_ratio:.2f})"
                if scipy_available and simplification_available and points_data and len(points_data) >= 3:
                    df_points_for_hull = pd.DataFrame(points_data)
                    if {'x_cm', 'y_cm', 'mesafe_cm'}.issubset(df_points_for_hull.columns):
                        df_valid_hull = df_points_for_hull[
                            df_points_for_hull['x_cm'].notna() & df_points_for_hull['y_cm'].notna() & (
                                        df_points_for_hull['mesafe_cm'] < 200) & (
                                        df_points_for_hull['mesafe_cm'] > 0.1)][['y_cm', 'x_cm']].copy()
                        if len(df_valid_hull) >= 3:
                            try:
                                points_np = df_valid_hull.values; hull = ConvexHull(
                                    points_np); shape_guess += f" Sınır: {len(hull.vertices)} köşe, Alan: {hull.area:.0f}cm²."
                            except scipy.spatial.qhull.QhullError as qe:
                                print(f"Convex Hull (Qhull) hatası: {qe}"); shape_guess += " (Sınır analizi hatası.)"
                            except Exception as e_hull:
                                print(f"Convex Hull genel hatası: {e_hull}"); shape_guess += " (Sınır analizi hatası.)"
                        else:
                            shape_guess += " (Sınır için yetersiz nokta.)"
                    else:
                        shape_guess += " (Sınır için veri eksik.)"
                elif not scipy_available or not simplification_available:
                    shape_guess += " (Gerekli kütüphane eksik.)"
            elif scan_status == 'completed_analysis':
                shape_guess = "Analiz verisi (G/D) eksik."
        except KeyError as ke:
            print(f"Şekil tahmini anahtar hatası: {ke}"); shape_guess = f"Tahmin veri hatası."
        except Exception as e:
            print(f"Şekil tahmini genel hata: {e}"); shape_guess = f"Tahmin genel hata."
        return f"Tahmin: {shape_guess}"


    # Diğer callback'ler (handle_stop_scan_script, update_system_card, export_csv, export_excel)
    # önceki halleriyle (dashboard_app_v4) kalabilir, çünkü dcc.Store'dan direkt veri okumuyorlar
    # veya anlık işlem yapıyorlar.
    @app.callback(Output('scan-status-message', 'children', allow_duplicate=True),
                  [Input('stop-scan-button', 'n_clicks')], prevent_initial_call=True)
    def handle_stop_scan_script(n_clicks_stop):  # ... (dashboard_app_v4'teki ile aynı) ...
        if n_clicks_stop == 0: return no_update; pid_to_kill = None
        if os.path.exists(PID_FILE_PATH_FOR_DASH):
            try:
                with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                    pid_str = pf.read().strip();
                if pid_str: pid_to_kill = int(pid_str)
            except (IOError, ValueError):
                pid_to_kill = None
        if pid_to_kill and is_process_running(pid_to_kill):
            try:
                os.kill(pid_to_kill, signal.SIGTERM);
                time.sleep(2.0)
                if is_process_running(pid_to_kill): os.kill(pid_to_kill, signal.SIGKILL); time.sleep(0.5)
                msg_suffix = ""
                if os.path.exists(PID_FILE_PATH_FOR_DASH):
                    try:
                        os.remove(PID_FILE_PATH_FOR_DASH); msg_suffix += " PID dosyası silindi."
                    except OSError as e:
                        msg_suffix += f" PID dosyası silinemedi ({e})."
                if os.path.exists(LOCK_FILE_PATH_FOR_DASH):
                    try:
                        os.remove(LOCK_FILE_PATH_FOR_DASH); msg_suffix += " Kilit dosyası silindi."
                    except OSError as e:
                        msg_suffix += f" Kilit dosyası silinemedi ({e})."
                if not is_process_running(pid_to_kill):
                    return dbc.Alert(f"Betik (PID: {pid_to_kill}) başarıyla durduruldu.{msg_suffix}", color="info",
                                     duration=5000)
                else:
                    return dbc.Alert(f"Betik (PID: {pid_to_kill}) durdurulamadı!{msg_suffix}", color="danger",
                                     duration=5000)
            except ProcessLookupError:
                return dbc.Alert(f"Betik (PID: {pid_to_kill}) zaten çalışmıyor.", color="warning", duration=4000)
            except Exception as e:
                return dbc.Alert(f"Betik (PID: {pid_to_kill}) durdurulurken hata: {e}", color="danger", duration=5000)
        else:
            msg = "Çalışan bir betik bulunamadı.";
            cleaned = False
            try:
                if os.path.exists(LOCK_FILE_PATH_FOR_DASH): os.remove(LOCK_FILE_PATH_FOR_DASH); cleaned = True
                if os.path.exists(PID_FILE_PATH_FOR_DASH): os.remove(PID_FILE_PATH_FOR_DASH); cleaned = True
            except OSError:
                pass
            if cleaned: msg += " Kalıntı dosyalar temizlendi."
            return dbc.Alert(msg, color="warning", duration=4000)


    @app.callback(
        [Output('script-status', 'children'), Output('script-status', 'className'), Output('cpu-usage', 'value'),
         Output('cpu-usage', 'label'), Output('ram-usage', 'value'), Output('ram-usage', 'label')],
        [Input('interval-component-system', 'n_intervals')]
    )
    def update_system_card(n_intervals):  # ... (dashboard_app_v4'teki ile aynı) ...
        script_status_text, status_class_name = "Beklemede", "text-secondary";
        pid = None
        if os.path.exists(PID_FILE_PATH_FOR_DASH):
            try:
                with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                    pid_str = pf.read().strip();
                if pid_str: pid = int(pid_str)
            except (IOError, ValueError):
                pass
        if pid and is_process_running(pid):
            script_status_text, status_class_name = f"Çalışıyor (PID: {pid})", "text-success"
        else:
            if os.path.exists(LOCK_FILE_PATH_FOR_DASH):
                script_status_text, status_class_name = "Durum Belirsiz (Kilit Var)", "text-warning"
            else:
                script_status_text, status_class_name = "Çalışmıyor", "text-danger"
        cpu_percent, ram_percent = 0.0, 0.0
        try:
            cpu_percent = round(psutil.cpu_percent(interval=0.1), 1); ram_percent = round(
                psutil.virtual_memory().percent, 1)
        except Exception as e:
            print(f"CPU/RAM (psutil) okuma hatası: {e}")
        return script_status_text, status_class_name, cpu_percent, f"{cpu_percent}%", ram_percent, f"{ram_percent}%"


    @app.callback(Output('download-csv', 'data'), [Input('export-csv-button', 'n_clicks')],
                  State('live-scan-data-store', 'data'), prevent_initial_call=True)
    def export_csv_callback(n_clicks, stored_data):  # ... (dashboard_app_v4'teki ile aynı) ...
        if n_clicks == 0 or not stored_data or not stored_data.get('scan_id'): return no_update
        latest_id = stored_data['scan_id'];
        conn, error = get_db_connection()
        if error or not conn: print(f"CSV Export DB Hatası: {error}"); return no_update
        if latest_id:
            try:
                df = pd.read_sql_query(f"SELECT * FROM scan_points WHERE scan_id = {latest_id} ORDER BY id ASC", conn)
                if not df.empty: return dcc.send_data_frame(df.to_csv, f"tarama_id_{latest_id}_noktalar.csv",
                                                            index=False)
            except (sqlite3.Error, pd.io.sql.DatabaseError) as e:
                print(f"CSV indirme DB hatası: {e}")
            except Exception as e:
                print(f"CSV indirme genel hata: {e}")
            finally:
                if conn: conn.close()
        return no_update


    @app.callback(Output('download-excel', 'data'), [Input('export-excel-button', 'n_clicks')],
                  State('live-scan-data-store', 'data'), prevent_initial_call=True)
    def export_excel_callback(n_clicks, stored_data):  # ... (dashboard_app_v4'teki ile aynı) ...
        if n_clicks == 0 or not stored_data or not stored_data.get('scan_id'): return no_update
        latest_id = stored_data['scan_id'];
        conn, error = get_db_connection()
        if error or not conn: print(f"Excel Export DB Hatası: {error}"); return no_update
        if latest_id:
            try:
                df_points = pd.read_sql_query(f"SELECT * FROM scan_points WHERE scan_id = {latest_id} ORDER BY id ASC",
                                              conn);
                df_scan_info = pd.read_sql_query(f"SELECT * FROM servo_scans WHERE id = {latest_id}", conn)
                if df_points.empty and df_scan_info.empty: return no_update
                excel_buffer = io.BytesIO()
                with pd.ExcelWriter(excel_buffer, engine='xlsxwriter') as writer:
                    if not df_points.empty: df_points.to_excel(writer, sheet_name=f'Scan_{latest_id}_Points',
                                                               index=False)
                    if not df_scan_info.empty: df_scan_info.to_excel(writer, sheet_name=f'Scan_{latest_id}_Info',
                                                                     index=False)
                excel_buffer.seek(0);
                return dcc.send_bytes(excel_buffer.read(), f"tarama_detaylari_id_{latest_id}.xlsx")
            except (sqlite3.Error, pd.io.sql.DatabaseError) as e:
                print(f"Excel indirme DB hatası: {e}")
            except Exception as e:
                print(f"Excel indirme genel hata: {e}")
            finally:
                if conn: conn.close()
        return no_update

else:
    print(f"!!!!!! [PID:{os.getpid()}] dash_apps.py: 'app' is None, callbacks are NOT being defined. !!!!!!")

if app:
    print(f"--- [PID:{os.getpid()}] dash_apps.py loaded successfully. 'app' object type: {type(app)} ---")
else:
    print(f"!!!!!! [PID:{os.getpid()}] dash_apps.py FAILED to load. 'app' object is None. !!!!!!")
