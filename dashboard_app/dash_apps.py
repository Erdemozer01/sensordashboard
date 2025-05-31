# dashboard_app/dash_apps.py (dcc.Store ile Yeniden Düzenlendi)
from django_plotly_dash import DjangoDash
import dash
from dash import html, dcc, Output, Input, State, no_update, dash_table
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
import psutil  # Sistem bilgilerini almak için
import scipy.spatial  # Convex Hull için (opsiyonel, yoksa hata vermemeli)

# --- Sabitler ---
try:
    # Bu dosyanın bulunduğu dizinin bir üst dizinini proje kök dizini olarak al
    PROJECT_ROOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
except NameError:  # Eğer __file__ tanımlı değilse (örn: interaktif bir ortamda)
    PROJECT_ROOT_DIR = os.getcwd()

DB_FILENAME = 'live_scan_data.sqlite3'
DB_PATH = os.path.join(PROJECT_ROOT_DIR, DB_FILENAME)
SENSOR_SCRIPT_FILENAME = 'sensor_script.py'
SENSOR_SCRIPT_PATH = os.path.join(PROJECT_ROOT_DIR, SENSOR_SCRIPT_FILENAME)  # Kök dizinde olduğunu varsayıyoruz
LOCK_FILE_PATH_FOR_DASH = '/tmp/sensor_scan_script.lock'
PID_FILE_PATH_FOR_DASH = '/tmp/sensor_scan_script.pid'

DEFAULT_UI_SCAN_EXTENT_ANGLE = 135  # Merkezden ± bu kadar açı
DEFAULT_UI_SCAN_STEP_ANGLE = 10

app = DjangoDash('RealtimeSensorDashboard', external_stylesheets=[dbc.themes.BOOTSTRAP])

# --- LAYOUT BİLEŞENLERİ (Değişiklik Yok) ---
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
        dbc.InputGroup([dbc.InputGroupText("Başlangıç Açısı (°)", style={"width": "200px"}),
                        # Değişti: Tarama Genişliği -> Başlangıç Açısı
                        dbc.Input(id="initial-angle-input", type="number", value=DEFAULT_UI_SCAN_EXTENT_ANGLE, min=-179,
                                  max=179, step=5)], className="mb-2"),
        dbc.InputGroup([dbc.InputGroupText("Bitiş Açısı (°)", style={"width": "200px"}),  # YENİ: Bitiş Açısı
                        dbc.Input(id="final-angle-input", type="number", value=-DEFAULT_UI_SCAN_EXTENT_ANGLE, min=-179,
                                  max=179, step=5)], className="mb-2"),
        dbc.InputGroup([dbc.InputGroupText("Adım Açısı (°)", style={"width": "200px"}),
                        dbc.Input(id="step-angle-input", type="number", value=DEFAULT_UI_SCAN_STEP_ANGLE, min=1, max=45,
                                  step=1)], className="mb-2"),
    ])
])

stats_panel = dbc.Card([
    dbc.CardHeader("Anlık Sensör Değerleri", className="bg-info text-white"),
    dbc.CardBody([
        dbc.Row([
            dbc.Col(html.Div([html.H6("Mevcut Açı:"), html.H4(id='current-angle', children="--°")]), width=4,
                    className="text-center"),
            dbc.Col(html.Div([html.H6("Mevcut Mesafe:"), html.H4(id='current-distance', children="-- cm")]), width=4,
                    className="text-center"),
            dbc.Col(html.Div([html.H6("Anlık Hız:"), html.H4(id='current-speed', children="-- cm/s")]), width=4,
                    className="text-center")
        ])])
], className="mb-3")

system_card = dbc.Card([
    dbc.CardHeader("Sistem Durumu", className="bg-secondary text-white"),
    dbc.CardBody([
        dbc.Row([dbc.Col(html.Div([html.H6("Sensör Betiği:"), html.H5(id='script-status', children="Beklemede")]))],
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
        ], className="mt-2"),
        html.Hr(style={'marginTop': '15px', 'marginBottom': '10px'}),
        dbc.Row([
            dbc.Col(dbc.Button("Ortam Şekil Tahmini", id="estimate-shape-button", color="info", className="w-100 mb-2"),
                    width=6),
            dbc.Col(html.P(id='shape-estimation-text', children="Tahmin için butona basın...", className="mt-2"),
                    width=6)
        ], className="mt-2 align-items-center")
    ])
])

visualization_tabs = dbc.Tabs([dbc.Tab(dcc.Graph(id='scan-map-graph', style={'height': '70vh'}), label="2D Harita"),
                               dbc.Tab(dcc.Graph(id='polar-graph', style={'height': '70vh'}), label="Polar Grafik"),
                               dbc.Tab(dcc.Graph(id='time-series-graph', style={'height': '70vh'}),
                                       label="Zaman Serisi"), dbc.Tab(
        dash_table.DataTable(id='raw-data-table', columns=[], data=[], page_size=20,
                             style_table={'overflowX': 'auto', 'height': '65vh', 'overflowY': 'auto'},
                             style_header={'backgroundColor': 'lightgrey', 'fontWeight': 'bold'},
                             style_cell={'textAlign': 'left', 'padding': '5px', 'minWidth': '80px', 'width': '100px',
                                         'maxWidth': '150px'},
                             filter_action="native", sort_action="native", sort_mode="multi"),
        label="Ham Veri Tablosu")])

app.layout = dbc.Container(fluid=True, children=[
    title_card,
    dbc.Row([
        dbc.Col([control_panel, dbc.Row(html.Div(style={"height": "15px"})), stats_panel,
                 dbc.Row(html.Div(style={"height": "15px"})), system_card, dbc.Row(html.Div(style={"height": "15px"})),
                 export_card], md=4, className="mb-3"),
        dbc.Col([visualization_tabs, dbc.Row(html.Div(style={"height": "15px"})), analysis_card], md=8)
    ]),
    dcc.Interval(id='interval-component-main', interval=1500, n_intervals=0),  # Ana veri güncelleme
    dcc.Interval(id='interval-component-system', interval=3000, n_intervals=0),  # Sistem durumu güncelleme
    dcc.Store(id='live-scan-data-store')  # <-- YENİ MERKEZİ VERİ DEPOSU
])


# --- HELPER FONKSİYONLAR (Değişiklik Yok) ---
def is_process_running(pid):
    if pid is None: return False
    try:
        os.kill(pid, 0)  # Sinyal göndermeden process var mı kontrol et
    except OSError:
        return False  # Process yok
    else:
        return True  # Process var


def get_db_connection():
    conn = None
    error_msg = None
    try:
        if not os.path.exists(DB_PATH):
            error_msg = f"Veritabanı dosyası ({DB_PATH}) bulunamadı."
            # print(f"DEBUG: Proje Kök Dizin: {PROJECT_ROOT_DIR}") # Hata ayıklama için
            # print(f"DEBUG: DB Yolu: {DB_PATH}") # Hata ayıklama için
        else:
            # Salt okunur modda bağlanmak, sensör betiğinin yazmasını engellemez.
            conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, timeout=5)
    except sqlite3.OperationalError as e:
        error_msg = f"DB Kilitli/Hata: {e}"
    except Exception as e:
        error_msg = f"DB Bağlantı Hatası: {e}"

    if error_msg:
        print(f"DEBUG get_db_connection: {error_msg}")
    return conn, error_msg


def get_latest_scan_id_from_db(conn_param=None):
    internal_conn_used = False
    conn_to_use = conn_param
    latest_id = None
    error_msg_helper = None

    if not conn_to_use:
        conn_to_use, error_msg_helper = get_db_connection()
        if error_msg_helper and not conn_to_use:
            print(f"DB Hatası (get_latest_scan_id - iç bağlantı): {error_msg_helper}")
            return None  # Bağlantı hatası varsa None dön
        internal_conn_used = True if conn_to_use else False

    if conn_to_use:
        try:
            # Önce 'running' veya 'completed_analysis' gibi aktif/son taramayı bul
            df_scan = pd.read_sql_query(
                "SELECT id FROM servo_scans WHERE status LIKE 'running%' OR status LIKE 'completed%' OR status LIKE 'terminated%' ORDER BY start_time DESC LIMIT 1",
                conn_to_use)
            if df_scan.empty:  # Aktif/son tarama yoksa, herhangi bir son taramayı al
                df_scan = pd.read_sql_query("SELECT id FROM servo_scans ORDER BY start_time DESC LIMIT 1", conn_to_use)

            if not df_scan.empty:
                latest_id = int(df_scan['id'].iloc[0])
        except Exception as e:
            print(f"Son tarama ID alınırken hata: {e}")
            latest_id = None  # Hata durumunda None
        finally:
            if internal_conn_used and conn_to_use:
                conn_to_use.close()
    return latest_id


# --- CALLBACK FONKSİYONLARI ---

# 1. YENİ: Merkezi Veri Çekme Callback'i
@app.callback(
    Output('live-scan-data-store', 'data'),
    Input('interval-component-main', 'n_intervals')
)
def fetch_data_for_store(n_intervals):
    """
    Bu callback, periyodik olarak veritabanına bağlanır,
    gerekli tüm verileri (noktalar ve tarama bilgisi) çeker ve dcc.Store içinde saklar.
    """
    # print(f"DEBUG fetch_data_for_store çağrıldı, interval: {n_intervals}") # Hata ayıklama
    conn, error = get_db_connection()
    if error or not conn:
        # print(f"DEBUG fetch_data_for_store: DB bağlantı hatası: {error}") # Hata ayıklama
        return {'points': [], 'info': {}, 'scan_id': None, 'error': str(error) if error else "Bağlantı yok"}

    latest_id = get_latest_scan_id_from_db(conn_param=conn)
    if not latest_id:
        conn.close()
        # print("DEBUG fetch_data_for_store: Gösterilecek tarama ID'si yok.") # Hata ayıklama
        return {'points': [], 'info': {}, 'scan_id': None, 'error': 'Tarama ID bulunamadı'}

    output_data = {'points': [], 'info': {}, 'scan_id': latest_id, 'error': None}
    try:
        df_points = pd.read_sql_query(
            f"SELECT id, angle_deg, mesafe_cm, hiz_cm_s, timestamp, x_cm, y_cm FROM scan_points WHERE scan_id = {latest_id} ORDER BY id ASC",
            conn)
        output_data['points'] = df_points.to_dict('records')

        df_scan_info = pd.read_sql_query(
            f"SELECT * FROM servo_scans WHERE id = {latest_id}",  # Tüm sütunları al
            conn)
        if not df_scan_info.empty:
            output_data['info'] = df_scan_info.to_dict('records')[0]

        # print(f"DEBUG fetch_data_for_store: {len(output_data['points'])} nokta, info: {'var' if output_data['info'] else 'yok'}") # Hata ayıklama
        return output_data

    except Exception as e:
        print(f"Store için veri çekme hatası (ID: {latest_id}): {e}")
        output_data['error'] = f"Veri çekme hatası: {e}"
        return output_data  # Hata olsa bile kısmi veri veya hata mesajı dönebilir
    finally:
        if conn:
            conn.close()


# 2. GÜNCELLENDİ: Grafik Callback'i artık Store'dan okuyor
@app.callback(
    [Output('scan-map-graph', 'figure'),
     Output('polar-graph', 'figure'),
     Output('time-series-graph', 'figure')],
    Input('live-scan-data-store', 'data')  # INPUT DEĞİŞTİ
)
def update_all_graphs(stored_data):
    # print(f"DEBUG update_all_graphs çağrıldı. Stored data: {'Var' if stored_data else 'Yok'}") # Hata ayıklama
    empty_fig_layout = go.Layout(title_text='Veri Bekleniyor...', paper_bgcolor='rgba(0,0,0,0)',
                                 plot_bgcolor='rgba(248,248,248,0.95)')
    empty_fig = go.Figure(layout=empty_fig_layout)

    if not stored_data or not stored_data.get('points'):
        # print("DEBUG update_all_graphs: Stored data veya points yok, boş grafikler.") # Hata ayıklama
        return empty_fig, empty_fig, empty_fig

    df_points = pd.DataFrame(stored_data['points'])
    scan_info = stored_data.get('info', {})
    scan_id = stored_data.get('scan_id', 'N/A')
    db_error = stored_data.get('error')

    title_suffix = f"(ID: {scan_id}, Durum: {scan_info.get('status', 'Bilinmiyor')})"
    if db_error: title_suffix += f" Hata: {db_error}"

    max_plot_dist = 200.0  # Grafiklerde gösterilecek maksimum mesafe

    # --- 2D Harita Grafiği ---
    fig_map = go.Figure(
        layout=go.Layout(title_text='2D Harita ' + title_suffix, xaxis_title="Yatay (cm)", yaxis_title="İleri (cm)",
                         yaxis_scaleanchor="x", yaxis_scaleratio=1, paper_bgcolor='rgba(0,0,0,0)',
                         plot_bgcolor='rgba(248,248,248,0.95)'))
    if not df_points.empty:
        df_valid_map = df_points[
            (df_points['mesafe_cm'] > 0.1) & (df_points['mesafe_cm'] < max_plot_dist) & df_points['x_cm'].notna() &
            df_points['y_cm'].notna()].copy()
        if not df_valid_map.empty:
            fig_map.add_trace(
                go.Scatter(x=df_valid_map['y_cm'], y=df_valid_map['x_cm'], mode='lines+markers', name='Sınır',
                           marker=dict(size=5, color=df_valid_map['mesafe_cm'], colorscale='Viridis', showscale=False),
                           line=dict(color='dodgerblue')))
            # Poligon alanı (opsiyonel, görselleştirme için)
            polygon_plot_x = [0.0] + list(df_valid_map['y_cm']) + [0.0]
            polygon_plot_y = [0.0] + list(df_valid_map['x_cm']) + [0.0]
            fig_map.add_trace(
                go.Scatter(x=polygon_plot_x, y=polygon_plot_y, fill="toself", fillcolor='rgba(0,176,246,0.2)',
                           line=dict(color='rgba(255,255,255,0)'), showlegend=False))
        fig_map.add_trace(go.Scatter(x=[0], y=[0], mode='markers', marker=dict(size=10, symbol='diamond', color='red'),
                                     name='Sensör'))

    # --- Polar Grafik ---
    fig_polar = go.Figure(layout=go.Layout(title_text='Polar Grafik ' + title_suffix, paper_bgcolor='rgba(0,0,0,0)',
                                           plot_bgcolor='rgba(248,248,248,0.95)'))
    if not df_points.empty:
        df_valid_polar = df_points[
            (df_points['mesafe_cm'] > 0.1) & (df_points['mesafe_cm'] < max_plot_dist) & df_points[
                'angle_deg'].notna()].copy()
        if not df_valid_polar.empty:
            fig_polar.add_trace(
                go.Scatterpolar(r=df_valid_polar['mesafe_cm'], theta=df_valid_polar['angle_deg'], mode='lines+markers',
                                name='Mesafe',
                                marker=dict(color=df_valid_polar['mesafe_cm'], colorscale='Viridis', showscale=True,
                                            colorbar_title_text="Mesafe(cm)")))
    fig_polar.update_layout(polar=dict(radialaxis=dict(visible=True, range=[0, max_plot_dist]),
                                       angularaxis=dict(direction="counterclockwise", ticksuffix="°")))

    # --- Zaman Serisi Grafiği ---
    fig_time = go.Figure(layout=go.Layout(title_text='Zaman Serisi - Mesafe ' + title_suffix, xaxis_title="Zaman",
                                          yaxis_title="Mesafe (cm)", paper_bgcolor='rgba(0,0,0,0)',
                                          plot_bgcolor='rgba(248,248,248,0.95)'))
    if not df_points.empty and 'timestamp' in df_points.columns:
        df_valid_time = df_points[(df_points['mesafe_cm'] > 0.1) & (df_points['mesafe_cm'] < max_plot_dist) & df_points[
            'timestamp'].notna()].copy()
        if not df_valid_time.empty:
            df_valid_time_sorted = df_valid_time.sort_values(by='timestamp')
            datetime_series = pd.to_datetime(df_valid_time_sorted['timestamp'], unit='s')
            fig_time.add_trace(go.Scatter(x=datetime_series, y=df_valid_time_sorted['mesafe_cm'], mode='lines+markers',
                                          name='Mesafe (cm)'))
            fig_time.update_xaxes(type='date', tickformat='%H:%M:%S')

    return fig_map, fig_polar, fig_time


# 3. GÜNCELLENDİ: Anlık Değerler Callback'i
@app.callback(
    [Output('current-angle', 'children'),
     Output('current-distance', 'children'),
     Output('current-speed', 'children')],
    Input('live-scan-data-store', 'data')  # INPUT DEĞİŞTİ
)
def update_realtime_values(stored_data):
    angle_str, distance_str, speed_str = "--°", "-- cm", "-- cm/s"
    if stored_data and stored_data.get('points') and len(stored_data['points']) > 0:
        try:
            last_point = stored_data['points'][-1]  # En son eklenen noktayı al
            angle_val = last_point.get('angle_deg')
            dist_val = last_point.get('mesafe_cm')
            speed_val = last_point.get('hiz_cm_s')

            angle_str = f"{angle_val:.0f}°" if pd.notnull(angle_val) else "--°"
            distance_str = f"{dist_val:.1f} cm" if pd.notnull(dist_val) else "-- cm"
            speed_str = f"{speed_val:.1f} cm/s" if pd.notnull(speed_val) else "-- cm/s"
        except IndexError:  # Eğer points listesi boşsa (çok nadir bir durum)
            pass
        except Exception as e:
            print(f"Anlık değerler güncellenirken hata: {e}")

    return angle_str, distance_str, speed_str


# 4. GÜNCELLENDİ: Analiz Paneli Callback'i
@app.callback(
    [Output('calculated-area', 'children'),
     Output('perimeter-length', 'children'),
     Output('max-width', 'children'),
     Output('max-depth', 'children')],
    Input('live-scan-data-store', 'data')  # INPUT DEĞİŞTİ
)
def update_analysis_panel_numeric(stored_data):
    area_str, perimeter_str, width_str, depth_str = "-- cm²", "-- cm", "-- cm", "-- cm"
    if stored_data and stored_data.get('info') and stored_data['info']:  # info boş değilse
        info = stored_data['info']
        area_val = info.get('hesaplanan_alan_cm2')
        perimeter_val = info.get('cevre_cm')
        width_val = info.get('max_genislik_cm')
        depth_val = info.get('max_derinlik_cm')

        area_str = f"{area_val:.2f} cm²" if pd.notnull(area_val) else "Hesaplanmadı"
        perimeter_str = f"{perimeter_val:.2f} cm" if pd.notnull(perimeter_val) else "Hesaplanmadı"
        width_str = f"{width_val:.2f} cm" if pd.notnull(width_val) else "Hesaplanmadı"
        depth_str = f"{depth_val:.2f} cm" if pd.notnull(depth_val) else "Hesaplanmadı"

    return area_str, perimeter_str, width_str, depth_str


# 5. GÜNCELLENDİ: Ham Veri Tablosu Callback'i
@app.callback(
    [Output('raw-data-table', 'data'),
     Output('raw-data-table', 'columns')],
    Input('live-scan-data-store', 'data')  # INPUT DEĞİŞTİ
)
def update_data_table(stored_data):
    # print(f"DEBUG update_data_table çağrıldı. Stored data: {'Var' if stored_data else 'Yok'}") # Hata ayıklama
    if not stored_data or not stored_data.get('points'):
        # print("DEBUG update_data_table: Stored data veya points yok, boş tablo.") # Hata ayıklama
        return [], [{"name": "Veri Yok / Hata", "id": "placeholder"}]

    df_table = pd.DataFrame(stored_data['points'])
    if df_table.empty:
        return [], [{"name": "Tarama Noktası Yok", "id": "no_points"}]

    try:
        # Zaman damgasını okunabilir formata çevir (milisaniye dahil)
        if 'timestamp' in df_table.columns:
            df_table['timestamp'] = pd.to_datetime(df_table['timestamp'], unit='s').dt.strftime('%H:%M:%S.%f').str[:-3]

        # İstenirse sayısal değerler burada yuvarlanabilir
        numeric_cols = ['mesafe_cm', 'hiz_cm_s', 'x_cm', 'y_cm', 'angle_deg']
        for col in numeric_cols:
            if col in df_table.columns:
                df_table[col] = df_table[col].round(2)  # 2 ondalık basamak

        table_data = df_table.to_dict('records')
        table_columns = [{"name": col.replace("_", " ").title(), "id": col} for col in df_table.columns]
        return table_data, table_columns
    except Exception as e:
        print(f"Veri tablosu oluşturulurken hata: {e}")
        return [], [{"name": f"Tablo Hatası: {str(e)[:30]}", "id": "table_error"}]


# --- BETİK BAŞLATMA/DURDURMA CALLBACK'LERİ (Değişiklik Yok) ---
@app.callback(
    Output('scan-status-message', 'children'),
    [Input('start-scan-button', 'n_clicks')],
    [State('initial-angle-input', 'value'),  # Değişti: scan-extent-input -> initial-angle-input
     State('final-angle-input', 'value'),  # YENİ: final-angle-input
     State('step-angle-input', 'value')],
    prevent_initial_call=True
)
def handle_start_scan_script(n_clicks_start, initial_angle_val, final_angle_val, step_angle_val):
    if n_clicks_start is None or n_clicks_start == 0:
        return no_update

    # Parametreleri doğrula
    initial_a = initial_angle_val if initial_angle_val is not None else DEFAULT_UI_SCAN_EXTENT_ANGLE
    final_a = final_angle_val if final_angle_val is not None else -DEFAULT_UI_SCAN_EXTENT_ANGLE
    step_a = step_angle_val if step_angle_val is not None else DEFAULT_UI_SCAN_STEP_ANGLE

    if not (-179 <= initial_a <= 179):
        return dbc.Alert("Geçersiz başlangıç açısı! (-179 ile 179 arası)", color="danger")
    if not (-179 <= final_a <= 179):
        return dbc.Alert("Geçersiz bitiş açısı! (-179 ile 179 arası)", color="danger")
    if initial_a == final_a:
        return dbc.Alert("Başlangıç ve bitiş açıları farklı olmalı!", color="danger")
    if not (1 <= step_a <= 45):
        return dbc.Alert("Geçersiz adım açısı! (1 ile 45 arası)", color="danger")

    current_pid = None
    if os.path.exists(PID_FILE_PATH_FOR_DASH):
        try:
            with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                pid_str = pf.read().strip()
            if pid_str: current_pid = int(pid_str)
        except ValueError:  # PID dosyası bozuksa
            print(f"PID dosyasındaki değer ({pid_str}) geçerli bir sayı değil.")
            current_pid = None
            try:
                os.remove(PID_FILE_PATH_FOR_DASH)  # Bozuk dosyayı sil
            except:
                pass
        except:  # Diğer okuma hataları
            current_pid = None

    if current_pid and is_process_running(current_pid):
        return dbc.Alert(f"Betik zaten çalışıyor (PID: {current_pid}). Önce durdurun.", color="warning")

    # Kilit dosyası varsa ve process çalışmıyorsa, kalıntı dosyaları temizle
    if os.path.exists(LOCK_FILE_PATH_FOR_DASH) and (not current_pid or not is_process_running(current_pid)):
        print("Kalıntı kilit dosyası bulundu, temizleniyor...")
        try:
            if os.path.exists(PID_FILE_PATH_FOR_DASH): os.remove(PID_FILE_PATH_FOR_DASH)
            if os.path.exists(LOCK_FILE_PATH_FOR_DASH): os.remove(LOCK_FILE_PATH_FOR_DASH)
        except OSError as e:
            return dbc.Alert(f"Kalıntı kilit/PID dosyası silinemedi: {e}. Manuel kontrol edin.", color="danger")

    try:
        # sys.executable Python yorumlayıcısının yolunu verir.
        # sensor_script.py'nin tam yolunu kullanmak daha güvenli.
        cmd = [
            sys.executable, SENSOR_SCRIPT_PATH,
            "--initial_goto_angle", str(initial_a),
            "--scan_end_angle", str(final_a),
            "--scan_step_angle", str(step_a)
        ]
        print(f"Dash: Sensör betiği başlatılıyor: {' '.join(cmd)}")
        # subprocess.Popen ayrı bir process başlatır ve beklemez.
        # start_new_session=True (Linux) veya creationflags=subprocess.CREATE_NEW_PROCESS_GROUP (Windows)
        # ana Dash process'i kapansa bile alt process'in çalışmaya devam etmesini sağlayabilir.
        # Ancak, Dash bir web sunucusu içinde çalışıyorsa bu daha karmaşık olabilir.
        # Şimdilik basit Popen yeterli.
        if os.name == 'posix':  # Linux, macOS
            process = subprocess.Popen(cmd, start_new_session=True)
        else:  # Windows
            process = subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)

        time.sleep(2.0)  # Betiğin PID dosyasını oluşturması için biraz bekle

        if os.path.exists(PID_FILE_PATH_FOR_DASH):
            new_pid = None
            try:
                with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                    pid_str = pf.read().strip()
                if pid_str: new_pid = int(pid_str)
                if new_pid and is_process_running(new_pid):
                    return dbc.Alert(f"Sensör betiği başlatıldı (PID: {new_pid}).", color="success")
                else:
                    return dbc.Alert(f"Betik başlatıldı ama PID ({new_pid}) ile process bulunamadı/çalışmıyor.",
                                     color="warning")
            except Exception as e_pid_read:
                return dbc.Alert(f"PID dosyası okuma hatası: {e_pid_read}", color="warning")
        else:
            # Betik Popen ile başladı ama PID dosyası oluşturmadıysa bir sorun var.
            # process.poll() ile durumu kontrol edilebilir. None ise hala çalışıyor.
            if process.poll() is not None:  # Betik hemen sonlandıysa
                # stderr veya stdout'u yakalayıp göstermek faydalı olabilir.
                return dbc.Alert(f"Betik başlatılamadı veya hemen sonlandı. Çıkış kodu: {process.poll()}",
                                 color="danger")
            return dbc.Alert("PID dosyası oluşmadı. Betik durumu belirsiz.", color="danger")

    except Exception as e:
        return dbc.Alert(f"Sensör betiği başlatma hatası: {e}", color="danger")
    return no_update  # Normalde buraya gelinmemeli


@app.callback(
    Output('scan-status-message', 'children', allow_duplicate=True),  # allow_duplicate önemli
    [Input('stop-scan-button', 'n_clicks')],
    prevent_initial_call=True
)
def handle_stop_scan_script(n_clicks_stop):
    if n_clicks_stop is None or n_clicks_stop == 0:
        return no_update

    pid_to_kill = None
    if os.path.exists(PID_FILE_PATH_FOR_DASH):
        try:
            with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                pid_str = pf.read().strip()
            if pid_str: pid_to_kill = int(pid_str)
        except:  # PID dosyası okuma hatası
            pid_to_kill = None

    if pid_to_kill and is_process_running(pid_to_kill):
        try:
            print(f"Sensör betiği (PID: {pid_to_kill}) durduruluyor (SIGTERM)...")
            os.kill(pid_to_kill, signal.SIGTERM)  # Önce kibarca durdurmayı dene
            time.sleep(2.0)  # Betiğin kapanması için bekle
            if is_process_running(pid_to_kill):  # Hala çalışıyorsa
                print(f"Betik (PID: {pid_to_kill}) SIGTERM'e yanıt vermedi, SIGKILL gönderiliyor...")
                os.kill(pid_to_kill, signal.SIGKILL)  # Zorla kapat
                time.sleep(0.5)

            # Kilit ve PID dosyalarının temizlenip temizlenmediğini kontrol et (betik kendisi yapmalı)
            if not is_process_running(pid_to_kill):
                msg = f"Sensör betiği (PID: {pid_to_kill}) durduruldu."
                cleaned_msg = ""
                if os.path.exists(PID_FILE_PATH_FOR_DASH):
                    try:
                        os.remove(PID_FILE_PATH_FOR_DASH); cleaned_msg += " PID dosyası silindi."
                    except:
                        cleaned_msg += " PID dosyası silinemedi."
                if os.path.exists(LOCK_FILE_PATH_FOR_DASH):
                    try:
                        os.remove(LOCK_FILE_PATH_FOR_DASH); cleaned_msg += " Kilit dosyası silindi."
                    except:
                        cleaned_msg += " Kilit dosyası silinemedi."
                return dbc.Alert(msg + cleaned_msg, color="info")
            else:
                return dbc.Alert(f"Betik (PID: {pid_to_kill}) durdurulamadı.", color="danger")

        except Exception as e:
            return dbc.Alert(f"Betik (PID: {pid_to_kill}) durdurulurken hata: {e}", color="danger")
    else:
        msg = "Çalışan sensör betiği bulunamadı."
        cleaned = False
        if os.path.exists(LOCK_FILE_PATH_FOR_DASH):
            try:
                os.remove(LOCK_FILE_PATH_FOR_DASH); cleaned = True
            except:
                pass
        if os.path.exists(PID_FILE_PATH_FOR_DASH):
            try:
                os.remove(PID_FILE_PATH_FOR_DASH); cleaned = True
            except:
                pass
        if cleaned: msg += " Kalıntı kilit/PID dosyaları temizlendi."
        return dbc.Alert(msg, color="warning")
    return no_update


# --- SİSTEM DURUMU CALLBACK'İ (Değişiklik Yok) ---
@app.callback(
    [Output('script-status', 'children'), Output('script-status', 'className'),  # className eklendi
     Output('cpu-usage', 'value'), Output('cpu-usage', 'label'),
     Output('ram-usage', 'value'), Output('ram-usage', 'label')],
    [Input('interval-component-system', 'n_intervals')]
)
def update_system_card(n_intervals):
    script_status_text = "Beklemede"
    status_class_name = "text-secondary"  # Varsayılan metin rengi
    pid = None

    if os.path.exists(PID_FILE_PATH_FOR_DASH):
        try:
            with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                pid_str = pf.read().strip()
            if pid_str: pid = int(pid_str)
        except:
            pid = None  # PID dosyası okunamadı

    if pid and is_process_running(pid):
        script_status_text = f"Çalışıyor (PID: {pid})"
        status_class_name = "text-success"  # Yeşil renk
    else:
        # PID dosyası yoksa veya process çalışmıyorsa
        if os.path.exists(LOCK_FILE_PATH_FOR_DASH):
            script_status_text = "Durum Belirsiz (Kilit Dosyası Var!)"
            status_class_name = "text-warning"  # Sarı renk
        else:
            script_status_text = "Çalışmıyor"
            status_class_name = "text-danger"  # Kırmızı renk

    cpu_percent, ram_percent = 0.0, 0.0
    try:
        cpu_percent = psutil.cpu_percent(interval=0.1)  # Kısa bir interval ile anlık kullanım
        virtual_mem = psutil.virtual_memory()
        ram_percent = virtual_mem.percent

        # Değerleri 0-100 arasına sıkıştır
        cpu_percent = round(max(0, min(100, cpu_percent)), 1)
        ram_percent = round(max(0, min(100, ram_percent)), 1)
    except Exception as e:
        print(f"CPU/RAM (psutil) okuma hatası: {e}")
        # Hata durumunda varsayılan değerler (0) kalır.

    return script_status_text, status_class_name, cpu_percent, f"{cpu_percent}%", ram_percent, f"{ram_percent}%"


# --- VERİ DIŞA AKTARMA CALLBACK'LERİ (Değişiklik Yok) ---
@app.callback(
    Output('download-csv', 'data'),
    [Input('export-csv-button', 'n_clicks')],
    State('live-scan-data-store', 'data'),  # Son tarama ID'sini store'dan alabiliriz
    prevent_initial_call=True
)
def export_csv_callback(n_clicks, stored_data):
    if n_clicks is None or n_clicks == 0 or not stored_data or not stored_data.get('scan_id'):
        return no_update

    latest_id = stored_data['scan_id']
    conn, error = get_db_connection()

    if conn and latest_id:
        try:
            df = pd.read_sql_query(f"SELECT * FROM scan_points WHERE scan_id = {latest_id} ORDER BY id ASC", conn)
            if not df.empty:
                return dcc.send_data_frame(df.to_csv, f"tarama_id_{latest_id}_noktalar.csv", index=False)
            else:
                print(f"CSV Export: ID {latest_id} için veri bulunamadı.")
        except Exception as e:
            print(f"CSV indirme hatası: {e}")
        finally:
            if conn: conn.close()
    return no_update


@app.callback(
    Output('download-excel', 'data'),
    [Input('export-excel-button', 'n_clicks')],
    State('live-scan-data-store', 'data'),  # Son tarama ID'sini store'dan alabiliriz
    prevent_initial_call=True
)
def export_excel_callback(n_clicks, stored_data):
    if n_clicks is None or n_clicks == 0 or not stored_data or not stored_data.get('scan_id'):
        return no_update

    latest_id = stored_data['scan_id']
    conn, error = get_db_connection()

    if conn and latest_id:
        try:
            df_points = pd.read_sql_query(f"SELECT * FROM scan_points WHERE scan_id = {latest_id} ORDER BY id ASC",
                                          conn)
            df_scan_info = pd.read_sql_query(f"SELECT * FROM servo_scans WHERE id = {latest_id}", conn)

            if df_points.empty and df_scan_info.empty:
                print(f"Excel Export: ID {latest_id} için veri bulunamadı.")
                return no_update

            excel_buffer = io.BytesIO()
            with pd.ExcelWriter(excel_buffer, engine='xlsxwriter') as writer:
                if not df_points.empty:
                    df_points.to_excel(writer, sheet_name=f'Scan_{latest_id}_Points', index=False)
                if not df_scan_info.empty:
                    df_scan_info.to_excel(writer, sheet_name=f'Scan_{latest_id}_Info', index=False)

            excel_buffer.seek(0)  # Buffer'ın başına git
            return dcc.send_bytes(excel_buffer.read(), f"tarama_detaylari_id_{latest_id}.xlsx")
        except Exception as e:
            print(f"Excel indirme hatası: {e}")
        finally:
            if conn: conn.close()
    return no_update


# --- ŞEKİL TAHMİNİ CALLBACK'İ (Değişiklik Yok) ---
@app.callback(
    Output('shape-estimation-text', 'children'),
    [Input('estimate-shape-button', 'n_clicks')],
    State('live-scan-data-store', 'data'),  # Analiz için store'daki veriyi kullan
    prevent_initial_call=True
)
def estimate_shape_callback(n_clicks, stored_data):
    if n_clicks is None or n_clicks == 0:
        return "Tahmin için butona basın..."

    if not stored_data or not stored_data.get('scan_id'):
        return "Analiz edilecek tarama verisi bulunamadı."

    scan_id = stored_data['scan_id']
    scan_info = stored_data.get('info', {})
    points_data = stored_data.get('points', [])

    shape_guess = "Şekil tahmini yapılamadı."

    if not scan_info or not points_data:
        return "Analiz için yeterli tarama bilgisi veya nokta verisi yok."

    try:
        max_g = scan_info.get('max_genislik_cm')
        max_d = scan_info.get('max_derinlik_cm')
        # Tarama ayarlarından scan_extent'i al (sensor_script'teki gibi initial_goto_angle ve scan_end_angle'dan hesaplanabilir)
        # Şimdilik varsayılan bir değer veya info'dan gelen bir değer kullanılabilir.
        # initial_angle = scan_info.get('initial_goto_angle_setting', DEFAULT_UI_SCAN_EXTENT_ANGLE)
        # final_angle = scan_info.get('scan_end_angle_setting', -DEFAULT_UI_SCAN_EXTENT_ANGLE)
        # scan_angle_range = abs(initial_angle - final_angle) # Toplam taranan açı aralığı

        if pd.notnull(max_g) and pd.notnull(max_d) and max_d > 0.01:  # max_d sıfır veya çok küçük değilse
            aspect_ratio = max_g / max_d
            # Bu kısım daha detaylı euristikler veya basit ML modelleri ile geliştirilebilir.
            if 0.8 < aspect_ratio < 1.25:  # Neredeyse kare/dairemsi
                shape_guess = "Oda benzeri veya geniş alan."
            elif aspect_ratio >= 1.25:  # Geniş ve yayvan
                shape_guess = "Geniş koridor veya yayvan bir engel."
            else:  # Dar ve uzun (aspect_ratio < 0.8)
                shape_guess = "Dar koridor veya uzun bir engel."

            shape_guess += f" (Genişlik/Derinlik Oranı: {aspect_ratio:.2f})"

            # Convex Hull analizi (scipy kuruluysa)
            if scipy_available and len(points_data) >= 3:
                df_points_for_hull = pd.DataFrame(points_data)
                # Sadece geçerli x,y noktalarını al
                df_valid_hull = df_points_for_hull[
                    df_points_for_hull['x_cm'].notna() & df_points_for_hull['y_cm'].notna() & (
                                df_points_for_hull['mesafe_cm'] < 200) & (df_points_for_hull['mesafe_cm'] > 0.1)][
                    ['y_cm', 'x_cm']].copy()
                if len(df_valid_hull) >= 3:
                    try:
                        points_np = df_valid_hull.values
                        hull = scipy.spatial.ConvexHull(points_np)
                        # hull.volume 2D'de alanı, hull.area 3D'de yüzey alanını verir.
                        # 2D için ConvexHull.area alanı verir.
                        shape_guess += f" Dışbükey Sınır: {len(hull.vertices)} köşe, Alan: {hull.area:.0f}cm²."
                    except Exception as e_hull:
                        print(f"Convex Hull hatası: {e_hull}")
                        shape_guess += " (Dış sınır analizi yapılamadı.)"
                else:
                    shape_guess += " (Dış sınır için yeterli nokta yok.)"
            elif not scipy_available:
                shape_guess += " (Scipy kurulu değil, dış sınır analizi atlandı.)"

        else:
            shape_guess = "Temel analiz verisi (genişlik/derinlik) eksik."

    except Exception as e:
        print(f"Şekil tahmini hatası: {e}")
        shape_guess = f"Şekil tahmini sırasında hata: {str(e)[:50]}"

    return shape_guess


# Scipy'nin varlığını kontrol et
scipy_available = True
try:
    import scipy.spatial
except ImportError:
    scipy_available = False
    print("UYARI: scipy.spatial modülü bulunamadı. Şekil tahminindeki Convex Hull analizi yapılamayacak.")

