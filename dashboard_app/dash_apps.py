# dashboard_app/dash_apps.py
from django_plotly_dash import DjangoDash
import dash
from dash import html, dcc, Output, Input, State, no_update
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import sqlite3
import pandas as pd
import os
import sys
import subprocess
import time
import math
import numpy as np
import signal
import io
import psutil

# --- Sabitler ---
try:
    PROJECT_ROOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
except NameError:
    PROJECT_ROOT_DIR = os.getcwd()
DB_FILENAME = 'live_scan_data.sqlite3';
DB_PATH = os.path.join(PROJECT_ROOT_DIR, DB_FILENAME)
SENSOR_SCRIPT_FILENAME = 'sensor_script.py';
SENSOR_SCRIPT_PATH = os.path.join(PROJECT_ROOT_DIR, SENSOR_SCRIPT_FILENAME)
LOCK_FILE_PATH_FOR_DASH = '/tmp/sensor_scan_script.lock';
PID_FILE_PATH_FOR_DASH = '/tmp/sensor_scan_script.pid'

DEFAULT_UI_SCAN_EXTENT_ANGLE = 135  # Merkezden her iki yana taranacak açı
DEFAULT_UI_SCAN_STEP_ANGLE = 10

app = DjangoDash('RealtimeSensorDashboard', external_stylesheets=[dbc.themes.BOOTSTRAP])

# --- LAYOUT BİLEŞENLERİ ---
title_card = dbc.Row([dbc.Col(html.H1("Dream Pi - 2D Alan Tarama Sistemi", className="text-center my-3"), width=12)])
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
        dbc.InputGroup([dbc.InputGroupText("Tarama Genişliği (Merkezden ±°)", style={"width": "200px"}),
                        dbc.Input(id="scan-extent-input", type="number", value=DEFAULT_UI_SCAN_EXTENT_ANGLE, min=10,
                                  max=179, step=5)], className="mb-2"),  # Değişti
        dbc.InputGroup([dbc.InputGroupText("Adım Açısı (°)", style={"width": "200px"}),
                        dbc.Input(id="step-angle-input", type="number", value=DEFAULT_UI_SCAN_STEP_ANGLE, min=1, max=45,
                                  step=1)], className="mb-2"),
    ])
])
# ... (stats_panel, system_card, export_card, analysis_card, visualization_tabs - Yanıt #50'deki gibi) ...
# (Bu bileşenlerin tanımları bir önceki tam kod cevabınızdaki (#50) gibi kalabilir.)
stats_panel = dbc.Card([dbc.CardHeader("Anlık Sensör Değerleri", className="bg-info text-white"),
                        dbc.CardBody([html.Div(id='realtime-values')])], className="mb-3")
system_card = dbc.Card([dbc.CardHeader("Sistem Durumu", className="bg-secondary text-white"),
                        dbc.CardBody([html.Div(id='system-status-values')])], className="mb-3")
export_card = dbc.Card([dbc.CardHeader("Veri Dışa Aktarma (En Son Tarama)", className="bg-light"), dbc.CardBody(
    [dbc.Button('En Son Taramayı CSV İndir', id='export-csv-button', color="primary", className="w-100 mb-2"),
     dcc.Download(id='download-csv'),
     dbc.Button('En Son Taramayı Excel İndir', id='export-excel-button', color="success", className="w-100"),
     dcc.Download(id='download-excel')])], className="mb-3")
analysis_card = dbc.Card([dbc.CardHeader("Tarama Analizi (En Son Tarama)", className="bg-dark text-white"),
                          dbc.CardBody(html.Div(id='analysis-output'))])
visualization_tabs = dbc.Tabs([dbc.Tab(dcc.Graph(id='scan-map-graph', style={'height': '75vh'}), label="2D Harita"),
                               dbc.Tab(dcc.Graph(id='polar-graph', style={'height': '75vh'}), label="Polar Grafik"),
                               dbc.Tab(dcc.Graph(id='time-series-graph', style={'height': '75vh'}),
                                       label="Zaman Serisi")])

app.layout = dbc.Container(fluid=True, children=[
    title_card,
    dbc.Row([
        dbc.Col([control_panel, dbc.Row(html.Div(style={"height": "15px"})), stats_panel,
                 dbc.Row(html.Div(style={"height": "15px"})), system_card, dbc.Row(html.Div(style={"height": "15px"})),
                 export_card], md=4, className="mb-3"),
        dbc.Col([visualization_tabs, dbc.Row(html.Div(style={"height": "15px"})), analysis_card], md=8)
    ]),
    dcc.Interval(id='interval-component-main', interval=1500, n_intervals=0),
    dcc.Interval(id='interval-component-system', interval=3000, n_intervals=0),
])


# --- HELPER FONKSİYONLAR (Yanıt #50'deki gibi) ---
# ... (is_process_running, get_db_connection, get_latest_scan_id_from_db) ...
def is_process_running(pid):  # Aynı
    if pid is None: return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    else:
        return True


def get_db_connection():  # Aynı
    try:
        if not os.path.exists(DB_PATH): return None, f"DB dosyası ({DB_PATH}) yok."
        conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, timeout=5);
        return conn, None
    except Exception as e:
        return None, f"DB Bağlantı Hatası: {e}"


def get_latest_scan_id_from_db(conn_param=None):  # Aynı
    internal_conn = False;
    conn_to_use = conn_param;
    latest_id = None
    if not conn_to_use: conn_to_use, error = get_db_connection();
    if error and not conn_to_use: print(f"DB Hatası (get_latest_scan_id): {error}"); return None
    internal_conn = (not conn_param and conn_to_use)
    if conn_to_use:
        try:
            df_scan = pd.read_sql_query(
                "SELECT id FROM servo_scans WHERE status = 'running' ORDER BY start_time DESC LIMIT 1", conn_to_use)
            if df_scan.empty: df_scan = pd.read_sql_query("SELECT id FROM servo_scans ORDER BY start_time DESC LIMIT 1",
                                                          conn_to_use)
            if not df_scan.empty: latest_id = int(df_scan['id'].iloc[0])
        except Exception as e:
            print(f"Son tarama ID alınırken hata: {e}")
        finally:
            if internal_conn and conn_to_use: conn_to_use.close()
    return latest_id


# --- CALLBACK FONKSİYONLARI ---
@app.callback(
    Output('scan-status-message', 'children'),
    [Input('start-scan-button', 'n_clicks')],
    [State('scan-extent-input', 'value'),  # Değişti
     State('step-angle-input', 'value')],
    prevent_initial_call=True
)
def handle_start_scan_script(n_clicks_start, scan_extent_val, step_angle_val):
    if n_clicks_start is None or n_clicks_start == 0: return dash.no_update

    extent_a = scan_extent_val if scan_extent_val is not None else DEFAULT_UI_SCAN_EXTENT_ANGLE
    step_a = step_angle_val if step_angle_val is not None else DEFAULT_UI_SCAN_STEP_ANGLE

    if not (10 <= extent_a <= 179):  # Örnek sınırlar
        return dbc.Alert("Geçersiz tarama genişliği (10-179 arası olmalı)!", color="danger")
    if not (1 <= step_a <= 45):
        return dbc.Alert("Geçersiz adım açısı (1-45 arası olmalı)!", color="danger")

    # PID ve Kilit Kontrolü (Aynı)
    # ... (Yanıt #50'deki gibi) ...
    current_pid = None;  # ... (PID/Lock kontrolü)
    if os.path.exists(PID_FILE_PATH_FOR_DASH):
        try:
            with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                pid_str = pf.read().strip();
            if pid_str: current_pid = int(pid_str)
        except:
            current_pid = None
    if current_pid and is_process_running(current_pid): return dbc.Alert(f"Betik çalışıyor (PID: {current_pid}).",
                                                                         color="warning")
    if os.path.exists(LOCK_FILE_PATH_FOR_DASH) and (not current_pid or not is_process_running(current_pid)):
        try:
            if os.path.exists(PID_FILE_PATH_FOR_DASH): os.remove(PID_FILE_PATH_FOR_DASH)
            if os.path.exists(LOCK_FILE_PATH_FOR_DASH): os.remove(LOCK_FILE_PATH_FOR_DASH)
        except OSError as e:
            return dbc.Alert(f"Kalıntı kilit silinemedi: {e}.", color="danger")

    try:
        python_executable = sys.executable
        if not os.path.exists(SENSOR_SCRIPT_PATH):
            return dbc.Alert(f"HATA: Sensör betiği ({SENSOR_SCRIPT_PATH}) bulunamadı!", color="danger")

        # sensor_script.py'ye yeni argümanları gönder
        cmd = [
            python_executable, SENSOR_SCRIPT_PATH,
            "--scan_extent", str(extent_a),  # Yeni argüman adı
            "--step_angle", str(step_a)
        ]
        print(f"Dash: Betik başlatılıyor: {' '.join(cmd)}")
        process = subprocess.Popen(cmd, start_new_session=True)
        time.sleep(2.5)
        # ... (PID dosyası kontrolü ve geri bildirim - Yanıt #50'deki gibi) ...
        if os.path.exists(PID_FILE_PATH_FOR_DASH):
            new_pid = None;
            try:
                with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                    pid_str = pf.read().strip()
                if pid_str: new_pid = int(pid_str)
                if new_pid and is_process_running(new_pid): return dbc.Alert(f"Betik başlatıldı (PID: {new_pid}).",
                                                                             color="success")
            except:
                pass
            return dbc.Alert(f"Betik PID ({new_pid}) ile process bulunamadı/okunamadı.", color="warning")
        else:
            return dbc.Alert(f"PID dosyası oluşmadı.", color="danger")
    except Exception as e:
        return dbc.Alert(f"Sensör betiği başlatılırken genel hata: {str(e)}", color="danger")
    return dash.no_update


@app.callback(
    Output('scan-status-message', 'children', allow_duplicate=True),
    # allow_duplicate=True, birden fazla callback aynı Output'u güncelleyebilir
    [Input('stop-scan-button', 'n_clicks')],
    prevent_initial_call=True
)
def handle_stop_scan_script(n_clicks_stop):
    if n_clicks_stop is None or n_clicks_stop == 0:
        return dash.no_update

    pid_to_kill = None
    if os.path.exists(PID_FILE_PATH_FOR_DASH):
        try:
            with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                pid_str = pf.read().strip()
                if pid_str: pid_to_kill = int(pid_str)
        except (FileNotFoundError, ValueError, TypeError):
            pid_to_kill = None

    if pid_to_kill and is_process_running(pid_to_kill):
        try:
            print(f"Dash: Sensör betiği (PID: {pid_to_kill}) için SIGTERM gönderiliyor...")
            os.kill(pid_to_kill, signal.SIGTERM)  # Önce nazikçe sonlandırmayı dene (atexit çalışır)
            time.sleep(2.0)  # atexit'in çalışması ve dosyaları silmesi için süre tanı
            if is_process_running(pid_to_kill):  # Hala çalışıyorsa
                print(f"Dash: Sensör betiği (PID: {pid_to_kill}) SIGTERM'e yanıt vermedi, SIGKILL gönderiliyor...")
                os.kill(pid_to_kill, signal.SIGKILL)  # Zorla kapat
                time.sleep(0.5)  # SIGKILL sonrası

            # Kilit ve PID dosyalarının silindiğini kontrol et
            if not os.path.exists(PID_FILE_PATH_FOR_DASH) and not os.path.exists(LOCK_FILE_PATH_FOR_DASH):
                return dbc.Alert(f"Sensör betiği (PID: {pid_to_kill}) durduruldu ve kilit dosyaları temizlendi.",
                                 color="info", duration=4000)
            else:
                # Eğer hala dosyalar varsa, manuel silmeyi deneyebiliriz veya kullanıcıya bildirebiliriz
                cleaned_after_kill = False
                if os.path.exists(PID_FILE_PATH_FOR_DASH): os.remove(PID_FILE_PATH_FOR_DASH); cleaned_after_kill = True
                if os.path.exists(LOCK_FILE_PATH_FOR_DASH): os.remove(
                    LOCK_FILE_PATH_FOR_DASH); cleaned_after_kill = True
                if cleaned_after_kill:
                    return dbc.Alert(
                        f"Sensör betiği (PID: {pid_to_kill}) durduruldu, kalan kilit/PID dosyaları temizlendi.",
                        color="info", duration=5000)
                else:
                    return dbc.Alert(
                        f"Sensör betiği (PID: {pid_to_kill}) durduruldu, ancak kilit/PID dosyaları hala mevcut olabilir. Manuel kontrol edin.",
                        color="warning", duration=5000)

        except ProcessLookupError:  # Process zaten yoksa (nadir durum)
            return dbc.Alert(f"Sensör betiği (PID: {pid_to_kill}) zaten çalışmıyor.", color="info", duration=4000)
        except Exception as e:
            return dbc.Alert(f"Sensör betiği (PID: {pid_to_kill}) durdurulurken hata: {e}", color="danger",
                             duration=5000)
    else:
        msg = "Çalışan bir sensör betiği bulunamadı veya PID dosyası geçersiz."
        cleaned_stale = False
        if os.path.exists(LOCK_FILE_PATH_FOR_DASH): os.remove(LOCK_FILE_PATH_FOR_DASH); cleaned_stale = True
        if os.path.exists(PID_FILE_PATH_FOR_DASH): os.remove(PID_FILE_PATH_FOR_DASH); cleaned_stale = True
        if cleaned_stale: msg += " Kalıntı kilit/PID dosyaları temizlendi."
        return dbc.Alert(msg, color="warning", duration=4000)
    return dash.no_update


@app.callback(
    [Output('calculated-area', 'children'),  # 1. Output
     Output('perimeter-length', 'children'),  # 2. Output
     Output('max-width', 'children'),  # 3. Output
     Output('max-depth', 'children')],  # 4. Output
    [Input('interval-component-main', 'n_intervals')]
    # Dropdown kaldırıldığı için selected_scan_id Input'u artık yok
)
def update_analysis_panel(n_intervals):
    print(f"--- update_analysis_panel tetiklendi --- n_intervals: {n_intervals}")  # Debug
    conn, error = get_db_connection()
    area_str, perimeter_str, width_str, depth_str = "-- cm²", "-- cm", "-- cm", "-- cm"  # Varsayılanlar

    latest_id = None
    if conn:  # Sadece bağlantı varsa ID almayı dene
        latest_id = get_latest_scan_id_from_db(conn_param=conn)

    print(f"Analiz için kullanılacak Tarama ID: {latest_id}")  # Debug

    if conn and latest_id:
        try:
            df_scan = pd.read_sql_query(
                f"SELECT hesaplanan_alan_cm2, cevre_cm, max_genislik_cm, max_derinlik_cm FROM servo_scans WHERE id = {latest_id}",
                conn
            )
            print(f"Analiz için çekilen df_scan verisi (ID: {latest_id}):\n{df_scan.to_string()}")  # Debug
            if not df_scan.empty:
                area_val = df_scan['hesaplanan_alan_cm2'].iloc[0]
                per_val = df_scan['cevre_cm'].iloc[0]
                w_val = df_scan['max_genislik_cm'].iloc[0]
                d_val = df_scan['max_derinlik_cm'].iloc[0]

                area_str = f"{area_val:.2f} cm²" if pd.notnull(area_val) else "Hesaplanmadı"
                perimeter_str = f"{per_val:.2f} cm" if pd.notnull(per_val) else "Hesaplanmadı"
                width_str = f"{w_val:.2f} cm" if pd.notnull(w_val) else "Hesaplanmadı"
                depth_str = f"{d_val:.2f} cm" if pd.notnull(d_val) else "Hesaplanmadı"
                print(
                    f"İşlenmiş analiz değerleri: Alan={area_str}, Çevre={perimeter_str}, Genişlik={width_str}, Derinlik={depth_str}")  # Debug
            else:
                print(f"Analiz: Tarama ID {latest_id} için servo_scans tablosunda veri yok veya sütunlar eksik.")
        except Exception as e:
            print(f"Analiz paneli DB sorgu hatası (ID: {latest_id}): {e}")
            area_str, perimeter_str, width_str, depth_str = "Hata", "Hata", "Hata", "Hata"
        # finally: conn.close() # get_latest_scan_id_from_db kendi bağlantısını açıp kapattıysa bu gereksiz
        # get_db_connection burada açtıysa en sonda kapatılmalı.
    elif error:  # get_db_connection'dan hata geldiyse
        print(f"DB Bağlantı Hatası (Analiz Paneli): {error}")
        area_str, perimeter_str, width_str, depth_str = "DB Yok", "DB Yok", "DB Yok", "DB Yok"
    else:  # Ne bağlantı var ne de hata mesajı (get_db_connection None döndürdüyse ve latest_id de None ise)
        print("Analiz: Gösterilecek tarama ID'si bulunamadı (DB bağlantısı sonrası).")

    # Layout'ta analysis-output ID'li Div'in children'ını güncelliyoruz.
    # Bu children, daha önce layout'ta tanımladığımız Row/Col yapısını içermeli.
    analysis_children_content = [
        dbc.Row([
            dbc.Col([html.H6("Hesaplanan Alan:"), html.H4(children=area_str)]),
            # id='calculated-area' kaldırıldı, doğrudan children
            dbc.Col([html.H6("Çevre Uzunluğu:"), html.H4(children=perimeter_str)])
        ]),
        dbc.Row([
            dbc.Col([html.H6("Max Genişlik:"), html.H4(children=width_str)]),
            dbc.Col([html.H6("Max Derinlik:"), html.H4(children=depth_str)])
        ], className="mt-2")
    ]
    print(f"Analiz paneli için döndürülen children: {analysis_children_content}")

    # Bu callback artık doğrudan analysis-output div'inin children'ını güncelliyor.
    # Output('calculated-area', 'children') gibi ayrı ayrı Output'lar yerine tek bir Output var.
    # Eğer layout'unuzda 'calculated-area', 'perimeter-length' vb. ID'lere sahip
    # ayrı H4'ler varsa ve onları ayrı ayrı güncellemek istiyorsanız, Output tanımı ve return
    # bir önceki cevaptaki (#50) gibi olmalı.
    # Şimdiki layout'unuzda (Yanıt #50'den gelen):
    # analysis_card = dbc.Card([dbc.CardHeader(...), dbc.CardBody(html.Div(id='analysis-output'))])
    # Bu durumda, analysis-output'un children'ını yukarıdaki `analysis_children_content` ile güncellemek doğru.

    if conn: conn.close()  # Eğer bu fonksiyon içinde açıldıysa bağlantıyı kapat
    return [analysis_children_content]  # Tek bir Output olduğu için tek bir değer listesi


# dashboard_app/dash_apps.py dosyasının bir parçası

# ... (Dosyanın başındaki diğer importlar ve sabit tanımlamaları aynı kalacak) ...
# ... (app = DjangoDash(...) tanımı) ...
# ... (app.layout tanımı) ...
# ... (Diğer helper fonksiyonlar: is_process_running, get_db_connection, get_latest_scan_id_from_db) ...
# ... (Diğer callback fonksiyonları: handle_start_scan_script, handle_stop_scan_script,
#      update_realtime_values, update_analysis_panel, update_system_card,
#      export_csv_callback, export_excel_callback) ...

@app.callback(
    [Output('scan-map-graph', 'figure'),
     Output('polar-graph', 'figure'),
     Output('time-series-graph', 'figure')],
    [Input('interval-component-main', 'n_intervals')]
    # prevent_initial_call=True YOK, sayfa ilk yüklendiğinde de çalışsın
)
def update_all_graphs(n_intervals):
    print(f"\n--- update_all_graphs ÇAĞRILDI (Interval No: {n_intervals}) ---")

    conn, error_msg_conn = get_db_connection()

    id_to_plot = None
    if conn:  # Sadece bağlantı varsa ID almayı dene
        id_to_plot = get_latest_scan_id_from_db(conn_param=conn)
        # get_latest_scan_id_from_db kendi açtığı bağlantıyı kapatır,
        # ama conn_param ile verileni kapatmaz. Bu conn'i sonda biz kapatacağız.

    # uirevision, kullanıcı etkileşimlerini (zoom, pan) korumak için.
    # ID değiştiğinde veya interval ile yeni veri geldiğinde (farklı n_intervals) grafik güncellenir.
    ui_revision_key = f"{str(id_to_plot if id_to_plot else 'no_scan_id')}_{n_intervals}"

    # Başlangıçta veya hata durumunda gösterilecek varsayılan figürler
    fig_map = go.Figure().update_layout(
        title_text='2D Kartezyen Harita (Veri bekleniyor...)',
        uirevision=ui_revision_key,
        plot_bgcolor='rgba(248,248,248,0.95)'  # Geçerli renk kodu
    )
    fig_polar = go.Figure().update_layout(
        title_text='Polar Grafik (Veri bekleniyor...)',
        uirevision=ui_revision_key,
        plot_bgcolor='rgba(248,248,248,0.95)'
    )
    fig_time = go.Figure().update_layout(
        title_text='Zaman Serisi - Mesafe (Veri bekleniyor...)',
        uirevision=ui_revision_key,
        plot_bgcolor='rgba(248,248,248,0.95)'
    )

    if error_msg_conn and not conn:  # get_db_connection bağlantı kuramadıysa
        print(f"Dash Grafik: DB bağlantı hatası (get_db_connection): {error_msg_conn}")
        fig_map.update_layout(title_text=f'2D Harita (DB Bağlantı Hatası)')
        fig_polar.update_layout(title_text=f'Polar Grafik (DB Bağlantı Hatası)')
        fig_time.update_layout(title_text=f'Zaman Serisi (DB Bağlantı Hatası)')
        # conn zaten None olmalı, kapatmaya gerek yok.
        return fig_map, fig_polar, fig_time

    if not id_to_plot:  # Çizilecek bir tarama ID'si yoksa (DB boş veya get_latest_scan_id_from_db None döndürdüyse)
        print("Dash Grafik: Çizilecek tarama ID'si bulunamadı. DB boş olabilir.")
        if conn: conn.close()  # Eğer açılmışsa bağlantıyı kapat
        return fig_map, fig_polar, fig_time

    print(f"Dash Grafik: Tarama ID {id_to_plot} için grafikler oluşturuluyor...")
    df_points = pd.DataFrame()
    df_scan_info = pd.DataFrame()

    # Bağlantıyı tekrar açmaya gerek yok, get_latest_scan_id_from_db'ye verdiğimiz conn hala açık olmalı
    # eğer get_db_connection başarılıysa. Eğer helper kendi açtıysa, o kapattı.
    # Bu yüzden en iyisi her sorgudan önce bağlantıyı kontrol etmek veya en başta açıp sonda kapatmak.
    # get_latest_scan_id_from_db conn_param alıyorsa bağlantıyı açık bırakır.

    if not conn:  # Eğer bir şekilde bağlantı kapandıysa veya hiç açılamadıysa tekrar dene
        conn, error_msg_conn = get_db_connection()
        if error_msg_conn:  # Yine hata varsa çık
            print(f"Dash Grafik: Grafik için veri çekilirken DB bağlantı hatası: {error_msg_conn}")
            return fig_map, fig_polar, fig_time

    try:
        if conn:  # Bağlantı varsa sorgula
            df_scan_info = pd.read_sql_query(
                f"SELECT status, start_time, scan_extent_angle_setting, step_angle_setting FROM servo_scans WHERE id = {id_to_plot}",
                conn
            )
            print(f"Dash Grafik: df_scan_info (ID: {id_to_plot}) - {len(df_scan_info)} satır bulundu.")

            df_points = pd.read_sql_query(
                f"SELECT angle_deg, mesafe_cm, x_cm, y_cm, timestamp FROM scan_points WHERE scan_id = {id_to_plot} ORDER BY id ASC",
                conn
            )
            print(f"Dash Grafik: df_points (ID: {id_to_plot}) - {len(df_points)} satır bulundu.")
            if not df_points.empty:
                print("Dash Grafik: df_points ilk 2 satır:")
                print(df_points.head(2).to_string())
        else:  # conn None ise (get_db_connection en başta hata verdiyse)
            print("Dash Grafik: Veri çekilemedi, DB bağlantısı yok.")
            # Hata mesajları zaten ayarlandı, boş grafikler dönecek

    except Exception as e:
        print(f"Dash Grafik: Veri çekme sırasında genel hata (ID: {id_to_plot}): {e}")
        # Hata durumunda df_points ve df_scan_info boş kalacak, grafik başlıkları güncellenecek
        error_text = f"Veri Çekme Hatası"
        fig_map.update_layout(title_text=f'2D Harita ({error_text})')
        fig_polar.update_layout(title_text=f'Polar Grafik ({error_text})')
        fig_time.update_layout(title_text=f'Zaman Serisi ({error_text})')
        if conn: conn.close()
        return fig_map, fig_polar, fig_time

    # ---- Grafik Oluşturma Mantığı ----
    scan_status_str = df_scan_info['status'].iloc[
        0] if not df_scan_info.empty and 'status' in df_scan_info.columns else "Bilinmiyor"
    start_time_epoch = df_scan_info['start_time'].iloc[
        0] if not df_scan_info.empty and 'start_time' in df_scan_info.columns else time.time()
    start_time_str = time.strftime('%H:%M:%S (%d-%m-%y)', time.localtime(start_time_epoch))
    scan_extent_str = str(int(df_scan_info['scan_extent_angle_setting'].iloc[
                                  0])) if not df_scan_info.empty and 'scan_extent_angle_setting' in df_scan_info.columns and pd.notnull(
        df_scan_info['scan_extent_angle_setting'].iloc[0]) else "?"
    scan_step_str = str(int(df_scan_info['step_angle_setting'].iloc[
                                0])) if not df_scan_info.empty and 'step_angle_setting' in df_scan_info.columns and pd.notnull(
        df_scan_info['step_angle_setting'].iloc[0]) else "?"

    title_suffix = f"(ID: {id_to_plot}, +/-{scan_extent_str}°, Adım:{scan_step_str}°, Başl: {start_time_str}, Dur: {scan_status_str})"

    if not df_points.empty:
        max_plot_dist = 200.0  # sensor_script.py'deki max_distance * 100 ile uyumlu olmalı

        df_points['mesafe_cm'] = pd.to_numeric(df_points['mesafe_cm'], errors='coerce')
        df_points['x_cm'] = pd.to_numeric(df_points['x_cm'], errors='coerce')
        df_points['y_cm'] = pd.to_numeric(df_points['y_cm'], errors='coerce')
        df_points['angle_deg'] = pd.to_numeric(df_points['angle_deg'], errors='coerce')
        df_points['timestamp'] = pd.to_numeric(df_points['timestamp'], errors='coerce')

        df_valid = df_points[
            (df_points['mesafe_cm'] > 0.1) &
            (df_points['mesafe_cm'] < max_plot_dist) &
            df_points['x_cm'].notna() &
            df_points['y_cm'].notna() &
            df_points['angle_deg'].notna() &
            df_points['timestamp'].notna()
            ].copy()
        print(f"Dash Grafik: Filtreleme sonrası geçerli nokta sayısı (ID: {id_to_plot}): {len(df_valid)}")

        if not df_valid.empty:
            # 1. 2D Kartezyen Harita
            fig_map.add_trace(go.Scatter(
                x=df_valid['y_cm'], y=df_valid['x_cm'], mode='lines+markers',
                name='Taranan Sınır',
                marker=dict(size=5, color=df_valid['mesafe_cm'], colorscale='Viridis', showscale=False),
                line=dict(color='dodgerblue', width=1.5)
            ))
            # Alanı doldurmak için poligon (orijin dahil)
            # Noktaların açısal olarak sıralı olması önemli (ORDER BY angle_deg ASC ile sağlandı)
            # Simetrik tarama (-X to +X) için bu poligon doğru bir sektörü gösterir.
            polygon_plot_x_coords = [0.0] + list(df_valid['y_cm'])
            polygon_plot_y_coords = [0.0] + list(df_valid['x_cm'])
            # Eğer tarama tam bir daire oluşturmuyorsa, poligonu kapatmak için son noktadan orijine dön
            if len(df_valid) > 1 and \
                    not (math.isclose(df_valid['angle_deg'].iloc[0] % 360, (df_valid['angle_deg'].iloc[-1] + (
                    df_scan_info['step_angle_setting'].iloc[0] if not df_scan_info.empty else 10)) % 360) \
                         and abs(df_valid['angle_deg'].iloc[-1] - df_valid['angle_deg'].iloc[0]) >= (360 - 2 * (
                            df_scan_info['step_angle_setting'].iloc[0] if not df_scan_info.empty else 10))):
                polygon_plot_x_coords.append(0.0)
                polygon_plot_y_coords.append(0.0)

            fig_map.add_trace(go.Scatter(
                x=polygon_plot_x_coords, y=polygon_plot_y_coords, fill="toself",
                fillcolor='rgba(0,176,246,0.2)',
                line=dict(color='rgba(255,255,255,0)'),  # Sınır çizgisini gösterme
                hoverinfo="skip", showlegend=False, name='Taranan Sektör Alanı'
            ))
            fig_map.add_trace(
                go.Scatter(x=[0], y=[0], mode='markers', marker=dict(size=10, symbol='diamond', color='red'),
                           name='Sensör Konumu'))
            fig_map.update_layout(
                title_text='2D Kartezyen Harita ' + title_suffix,
                xaxis_title="Yatay Yayılım (cm)", yaxis_title="İleri Mesafe (cm)",
                yaxis_scaleanchor="x", yaxis_scaleratio=1,
                margin=dict(l=40, r=40, t=60, b=40)
            )

            # 2. Polar Grafik
            fig_polar.add_trace(go.Scatterpolar(
                r=df_valid['mesafe_cm'], theta=df_valid['angle_deg'], mode='lines+markers',
                name='Mesafe Profili',
                marker=dict(color=df_valid['mesafe_cm'], colorscale='Viridis', showscale=True,
                            colorbar_title_text="Mesafe(cm)"),
                line_color='deepskyblue'
            ))
            # Polar eksen aralığını veriye göre veya sabit ayarla
            min_angle_data = df_valid['angle_deg'].min()
            max_angle_data = df_valid['angle_deg'].max()
            fig_polar.update_layout(
                title_text='Polar Grafik ' + title_suffix,
                polar=dict(
                    radialaxis=dict(visible=True, range=[0, max_plot_dist], ticksuffix=" cm"),
                    angularaxis=dict(direction="counterclockwise", thetaunit="degrees", ticksuffix="°",
                                     # Taranan açı aralığını göstermek için:
                                     # range=[min_angle_data if pd.notnull(min_angle_data) else -180,
                                     #        max_angle_data if pd.notnull(max_angle_data) else 180]
                                     )
                )
            )

            # 3. Zaman Serisi (Mesafe vs Zaman)
            if 'timestamp' in df_valid.columns:
                df_time_series = df_valid.sort_values(by='timestamp')
                datetime_series = pd.to_datetime(df_time_series['timestamp'], unit='s')
                fig_time.add_trace(go.Scatter(
                    x=datetime_series, y=df_time_series['mesafe_cm'],
                    mode='lines+markers', name='Mesafe (cm)', marker_color='green'
                ))
                fig_time.update_xaxes(type='date', tickformat='%H:%M:%S')  # Milisaniye için .%L eklenebilir
            fig_time.update_layout(
                title_text='Zaman Serisi - Mesafe ' + title_suffix,
                xaxis_title="Ölçüm Zamanı", yaxis_title="Mesafe (cm)",
                margin=dict(l=40, r=40, t=60, b=40)
            )
        else:
            print(f"Dash Grafik: Tarama ID {id_to_plot} için geçerli nokta bulunamadı (filtreleme sonrası).")
            fig_map.update_layout(title_text='2D Harita ' + title_suffix + " (Geçerli Veri Yok)")
            fig_polar.update_layout(title_text='Polar Grafik ' + title_suffix + " (Geçerli Veri Yok)")
            fig_time.update_layout(title_text='Zaman Serisi ' + title_suffix + " (Geçerli Veri Yok)")
    else:
        print(f"Dash Grafik: Tarama ID {id_to_plot} için nokta verisi bulunamadı.")
        fig_map.update_layout(title_text='2D Harita ' + title_suffix + " (Nokta Verisi Yok)")
        fig_polar.update_layout(title_text='Polar Grafik ' + title_suffix + " (Nokta Verisi Yok)")
        fig_time.update_layout(title_text='Zaman Serisi ' + title_suffix + " (Nokta Verisi Yok)")

    if conn: conn.close()  # En sonda, bu callback için açılan bağlantıyı kapat
    print(f"Dash Grafik: Grafik güncelleme tamamlandı. Gösterilen ID: {id_to_plot}")
    return fig_map, fig_polar, fig_time

@app.callback(
    [Output('script-status', 'children'),  # Sensör betiği durum metni
     Output('script-status', 'className'),  # Durum metninin rengi için Bootstrap sınıfı
     Output('cpu-usage', 'value'),  # CPU kullanım progress bar değeri
     Output('cpu-usage', 'label'),  # CPU kullanım progress bar etiketi (% değeri)
     Output('ram-usage', 'value'),  # RAM kullanım progress bar değeri
     Output('ram-usage', 'label')],  # RAM kullanım progress bar etiketi (% değeri)
    [Input('interval-component-system', 'n_intervals')]  # Periyodik güncelleme için
)
def update_system_card(n_intervals):
    print(f"--- update_system_card tetiklendi (Interval: {n_intervals}) ---")

    # --- Sensör Betiği Durumunu Kontrol Et ---
    script_status_text = "Beklemede"
    status_class_name = "text-secondary"  # Varsayılan renk (Bootstrap: gri)
    pid = None

    if os.path.exists(PID_FILE_PATH_FOR_DASH):
        try:
            with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                pid_str = pf.read().strip()
                if pid_str:  # Dosya boş değilse
                    pid = int(pid_str)
        except (FileNotFoundError, ValueError, TypeError) as e:
            print(f"Sistem Durumu: PID dosyası ({PID_FILE_PATH_FOR_DASH}) okunamadı veya geçersiz: {e}")
            pid = None

    if pid and is_process_running(pid):
        script_status_text = "Çalışıyor"
        status_class_name = "text-success"  # Yeşil renk
    else:
        # Eğer PID dosyası yoksa veya PID ile process bulunamadıysa, kilit dosyasına da bakabiliriz
        if os.path.exists(LOCK_FILE_PATH_FOR_DASH):
            script_status_text = "Durum Belirsiz (Kilit Var)"
            status_class_name = "text-warning"  # Sarı renk
        else:
            script_status_text = "Çalışmıyor"
            status_class_name = "text-danger"  # Kırmızı renk

    print(f"Sistem Durumu: Betik durumu: {script_status_text} (PID: {pid})")

    # --- CPU ve RAM Kullanımını psutil ile Al ---
    cpu_percent_val = 0.0
    ram_percent_val = 0.0

    try:
        # interval=None anlık kullanımı verir, interval=0.1 engellemeyen bir ortalama alır.
        cpu_percent_val = psutil.cpu_percent(interval=0.1)
        virtual_mem = psutil.virtual_memory()
        ram_percent_val = virtual_mem.percent

        # Değerlerin 0-100 aralığında olduğundan emin ol
        cpu_percent_val = round(max(0.0, min(100.0, cpu_percent_val)), 1)
        ram_percent_val = round(max(0.0, min(100.0, ram_percent_val)), 1)
        print(f"Sistem Metrikleri: CPU={cpu_percent_val}%, RAM={ram_percent_val}%")
    except Exception as e:
        print(f"Sistem Durumu: CPU/RAM bilgileri (psutil) alınırken hata: {e}")
        # Hata durumunda varsayılan değerler (0.0) kalır

    cpu_label = f"{cpu_percent_val}%"
    ram_label = f"{ram_percent_val}%"

    return script_status_text, status_class_name, \
        cpu_percent_val, cpu_label, \
        ram_percent_val, ram_label
# handle_stop_scan_script (Yanıt #50'deki gibi)
# update_realtime_values (Yanıt #50'deki gibi)
# update_all_graphs (Yanıt #50'deki gibi, dropdown input'u olmadan en son taramayı alır)
# update_analysis_panel (Yanıt #50'deki gibi, dropdown input'u olmadan en son taramayı alır)
# update_system_card (Yanıt #50'deki gibi, psutil ile)
# export_csv_callback (Yanıt #50'deki gibi, dropdown state'i olmadan en son taramayı alır)
# export_excel_callback (Yanıt #50'deki gibi, dropdown state'i olmadan en son taramayı alır)

# ÖNEMLİ: Diğer tüm callback fonksiyonlarının (update_realtime_values, update_all_graphs, vb.)
# tam ve güncel hallerini bir önceki tam kod cevabınızdan (Yanıt #50) alıp
# buraya dikkatlice kopyalamanız gerekmektedir.
# Özellikle, artık bir 'scan-select-dropdown' olmadığı için, bu callback'lerin
# Input/State listelerinden bu dropdown'a yapılan referansları çıkarmanız ve
# bunun yerine `get_latest_scan_id_from_db()` helper fonksiyonunu kullanarak
# en son tarama ID'sini alıp ona göre işlem yapmaları gerekir.
# Yanıt #50'deki kod zaten bu mantıkla (dropdown olmadan) yazılmıştı.