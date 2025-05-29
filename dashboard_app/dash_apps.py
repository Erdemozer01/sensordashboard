# dashboard_app/dash_apps.py
from django_plotly_dash import DjangoDash
import dash
from dash import html, dcc
from dash.dependencies import Input, Output, State
import plotly.graph_objects as go
import sqlite3
import pandas as pd
import os
import sys
import subprocess
import time
import math
import numpy as np
import dash_bootstrap_components as dbc

# --- Sabitler ---
PROJECT_ROOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
DB_FILENAME = 'live_scan_data.sqlite3'
DB_PATH = os.path.join(PROJECT_ROOT_DIR, DB_FILENAME)
SENSOR_SCRIPT_FILENAME = 'sensor_script.py'
SENSOR_SCRIPT_PATH = os.path.join(PROJECT_ROOT_DIR, SENSOR_SCRIPT_FILENAME)
LOCK_FILE_PATH_FOR_DASH = '/tmp/sensor_scan_script.lock'
PID_FILE_PATH_FOR_DASH = '/tmp/sensor_scan_script.pid'

app = DjangoDash('RealtimeSensorDashboard', external_stylesheets=[dbc.themes.BOOTSTRAP])

app.layout = dbc.Container(fluid=True, children=[
    dbc.Row(
        [
            dbc.Col(html.H1("Dream Pi - 2D Alan Tarama", className="text-center my-3"),
                    width=12)
        ]
    ),

    dbc.Row([
        dbc.Col([
            html.Div([html.Button('2D Taramayı Başlat', id='start-scan-button', n_clicks=0,
                                  className="btn btn-success btn-lg w-100 mb-3")]),
            html.Div(html.Span(id='scan-status-message',
                               style={'fontSize': '16px', 'display': 'block', 'marginBottom': '20px',
                                      'minHeight': '20px'}), className="text-center"),
            html.Div(id='scan-summary-realtime',
                     style={'padding': '15px', 'fontSize': '15px', 'border': '1px solid #ddd', 'borderRadius': '5px',
                            'backgroundColor': '#f9f9f9', 'minHeight': '180px'})  # Yükseklik artırıldı
        ], md=4),
        dbc.Col([dcc.Graph(id='scan-map-graph', style={'height': '80vh'})], md=8)  # Yükseklik ayarlandı
    ]),
    dcc.Interval(id='interval-component-scan', interval=1500, n_intervals=0),
])


def is_process_running(pid):
    if pid is None: return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    else:
        return True


@app.callback(
    Output('scan-status-message', 'children'),
    [Input('start-scan-button', 'n_clicks')],
    prevent_initial_call=True
)
def handle_start_scan_script(n_clicks):
    ctx = dash.callback_context;
    if not ctx.triggered or n_clicks == 0: return dash.no_update
    # ... (Bu callback fonksiyonunun içeriği bir önceki cevaptakiyle aynı kalabilir - Yanıt #30) ...
    current_pid = None
    if os.path.exists(PID_FILE_PATH_FOR_DASH):
        try:
            with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                pid_str = pf.read().strip();
            if pid_str: current_pid = int(pid_str)
        except:
            current_pid = None
    if current_pid and is_process_running(current_pid): return "Sensör zaten çalışıyor..."
    if os.path.exists(LOCK_FILE_PATH_FOR_DASH) and (not current_pid or not is_process_running(current_pid)):
        try:
            if os.path.exists(PID_FILE_PATH_FOR_DASH): os.remove(PID_FILE_PATH_FOR_DASH)
            if os.path.exists(LOCK_FILE_PATH_FOR_DASH): os.remove(LOCK_FILE_PATH_FOR_DASH)
        except OSError as e:
            return f"Kalıntı kilit/PID silinirken hata: {e}."
    try:
        python_executable = sys.executable
        if not os.path.exists(SENSOR_SCRIPT_PATH): return f"HATA: Sensör betiği bulunamadı: {SENSOR_SCRIPT_PATH}"
        process = subprocess.Popen([python_executable, SENSOR_SCRIPT_PATH], start_new_session=True)
        time.sleep(2.5)
        if os.path.exists(PID_FILE_PATH_FOR_DASH):
            new_pid = None;
            try:
                with open(PID_FILE_PATH_FOR_DASH, 'r') as pf_new:
                    pid_str_new = pf_new.read().strip();
                if pid_str_new: new_pid = int(pid_str_new)
                if new_pid and is_process_running(new_pid):
                    return f"Tarama Başlatıldı"
                else:
                    return f"Sensör betiği başlatıldı ama PID ({new_pid}) ile process bulunamadı."
            except Exception as e:
                return f"PID okunurken hata: {e}"
        else:
            return f"PID dosyası ({PID_FILE_PATH_FOR_DASH}) oluşmadı. Betik loglarını kontrol edin."
    except Exception as e:
        return f"Sensör betiği başlatılırken hata: {str(e)}"
    return dash.no_update


@app.callback(
    [Output('scan-map-graph', 'figure'),
     Output('scan-summary-realtime', 'children')],
    [Input('interval-component-scan', 'n_intervals')]
)
def update_scan_map_graph(n_intervals):
    conn = None
    df_points = pd.DataFrame()
    df_scan_info = pd.DataFrame()  # Ana tarama bilgisini (alan dahil) tutmak için
    error_message_div = []
    fig_map = go.Figure()
    summary_children = [html.P("Veri bekleniyor...")]
    latest_scan_id = None  # Tanımla
    latest_scan_status = "Bilinmiyor"
    latest_scan_start_time_str = "N/A"
    hesaplanan_alan_cm2_str = "Hesaplanmadı"

    try:
        if not os.path.exists(DB_PATH):
            raise FileNotFoundError(f"Veritabanı dosyası ({DB_PATH}) bulunamadı.")

        conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, timeout=5)
        df_scan_info = pd.read_sql_query(
            "SELECT id, status, start_time, hesaplanan_alan_cm2 FROM servo_scans ORDER BY start_time DESC LIMIT 1", conn
        )

        if not df_scan_info.empty:
            latest_scan_id = int(df_scan_info['id'].iloc[0])
            latest_scan_status = df_scan_info['status'].iloc[0]
            latest_scan_start_epoch = df_scan_info['start_time'].iloc[0]
            latest_scan_start_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(latest_scan_start_epoch))

            alan_db = df_scan_info['hesaplanan_alan_cm2'].iloc[0]
            if alan_db is not None:
                hesaplanan_alan_cm2_str = f"{alan_db:.2f} cm²"

            # x_cm ve y_cm sütunlarını da çekiyoruz
            df_points = pd.read_sql_query(
                f"SELECT angle_deg, mesafe_cm, x_cm, y_cm FROM scan_points WHERE scan_id = {latest_scan_id} ORDER BY id ASC",
                conn
            )
        else:
            error_message_div = [html.P(f"Veritabanında hiç tarama kaydı bulunamadı.", style={'color': 'orange'})]

    except sqlite3.OperationalError as e_sql:
        msg = f"Veritabanı okuma hatası: {e_sql}.";
        error_message_div = [html.P(msg, style={'color': 'red'})]
    except FileNotFoundError as e_fnf:
        msg = str(e_fnf);
        error_message_div = [html.P(msg, style={'color': 'orange'})]
    except Exception as e_gen:
        msg = f"Veri okunurken bilinmeyen bir hata: {e_gen}";
        error_message_div = [html.P(msg, style={'color': 'red'})]
    finally:
        if conn: conn.close()

    if error_message_div:
        summary_children = error_message_div
        fig_map.update_layout(title_text='2D Tarama Haritası (Hata/Veri Yok)')
    elif not df_points.empty and 'x_cm' in df_points.columns and 'y_cm' in df_points.columns:
        df_points_valid = df_points.copy()
        # sensor_script.py zaten x_cm, y_cm ürettiği için burada tekrar hesaplamaya gerek yok.
        # Sadece geçerli mesafeleri filtreleyebiliriz (isteğe bağlı)
        # max_plot_distance = 200.0
        # df_points_valid = df_points[(df_points['mesafe_cm'] > 0.1) & (df_points['mesafe_cm'] < max_plot_distance)].copy()

        if not df_points_valid.empty:
            # Noktaları çizdir
            fig_map.add_trace(go.Scatter(
                x=df_points_valid['y_cm'],  # Yatay eksende y_cm
                y=df_points_valid['x_cm'],  # Dikey eksende x_cm (ileri yön)
                mode='lines+markers',
                name='Taranan Sınır',
                line=dict(color='rgba(0,100,80,0.7)', width=2),
                marker=dict(size=5, color=df_points_valid['mesafe_cm'], colorscale='Viridis', showscale=True,
                            colorbar_title_text="Mesafe (cm)")
            ))
            # Alanı doldurmak için poligonun başlangıç ve bitişine orijini ekle
            # Bu, taranan sektörün alanını görselleştirir
            filled_x = [0] + list(df_points_valid['y_cm']) + [df_points_valid['y_cm'].iloc[-1],
                                                              0]  # Son noktadan tekrar orijine gibi
            filled_y = [0] + list(df_points_valid['x_cm']) + [0, 0]  # İlk ve son x_cm'den orijine

            # Eğer tarama 0-180 ise ve noktalar sıralıysa, şu daha doğru bir poligon oluşturur:
            polygon_plot_x = [0] + list(df_points_valid['y_cm'])
            polygon_plot_y = [0] + list(df_points_valid['x_cm'])
            # Eğer son nokta ile orijin arasında da bir çizgi istiyorsanız ve bu kapalı bir alan oluşturuyorsa:
            # polygon_plot_x.append(0)
            # polygon_plot_y.append(0)

            fig_map.add_trace(go.Scatter(
                x=polygon_plot_x,
                y=polygon_plot_y,
                fill="toself",  # Poligonu doldur
                fillcolor='rgba(0,176,246,0.2)',
                line=dict(color='rgba(255,255,255,0)'),  # Kenar çizgisini gösterme
                hoverinfo="skip",
                showlegend=False,
                name='Taranan Alan'
            ))

            fig_map.add_trace(
                go.Scatter(x=[0], y=[0], mode='markers', marker=dict(size=10, symbol='diamond', color='red'),
                           name='Sensör Konumu'))

            fig_map.update_layout(
                title_text=f'Alan Tarama',
                xaxis_title="Yatay Yayılım (cm)", yaxis_title="İleri Mesafe (cm)",
                yaxis_scaleanchor="x", yaxis_scaleratio=1,
                width=None, height=650,
                margin=dict(l=50, r=50, b=50, t=80), plot_bgcolor='rgba(248,248,248,1)',
                legend=dict(yanchor="top", y=0.99, xanchor="center", x=0.01)
            )
        else:
            fig_map.update_layout(title_text=f'Çizilecek geçerli nokta yok')

        summary_children = [html.H4("Tarama Özeti:", style={'marginTop': '0px', 'marginBottom': '10px'})]
        if latest_scan_id is not None:
            summary_children.append(html.P([f"Son Tarama ID: {latest_scan_id}"]))
            summary_children.append(html.P(f"Hesaplanan Sektör Alanı: {hesaplanan_alan_cm2_str}"))  # Alanı göster
        summary_children.append(html.P(f"Toplam Okunan Nokta Sayısı: {len(df_points)}"))
        if not df_points_valid.empty:
            summary_children.append(html.P(f"Grafiğe Çizilen Nokta: {len(df_points_valid)}"))
            summary_children.append(html.P(f"Min Algılanan Mesafe: {df_points_valid['mesafe_cm'].min():.2f} cm"))
            summary_children.append(html.P(f"Max Algılanan Mesafe: {df_points_valid['mesafe_cm'].max():.2f} cm"))
    else:
        fig_map.update_layout(title_text='2D Tarama Haritası (Veri Bekleniyor)')
        if latest_scan_id is not None:
            summary_children = [
                html.P(
                "Bu tarama için henüz nokta bulunamadı.")
            ]
        else:
            summary_children = [html.P("Aktif tarama veya görüntülenecek veri yok.")]

    return fig_map, summary_children
