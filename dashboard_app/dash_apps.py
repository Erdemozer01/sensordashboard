# dashboard_app/dash_apps.py
from django_plotly_dash import DjangoDash
import dash
from dash import html, dcc  # Güncel importlar
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

PROJECT_ROOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')

# sensor_script.py'deki DB_NAME_ONLY ile aynı olmalı!
DB_FILENAME = 'live_scan_data.sqlite3'
DB_PATH = os.path.join(PROJECT_ROOT_DIR, DB_FILENAME)

SENSOR_SCRIPT_FILENAME = 'sensor_script.py'
SENSOR_SCRIPT_PATH = os.path.join(PROJECT_ROOT_DIR, SENSOR_SCRIPT_FILENAME)

# Kilit ve PID dosyaları için mutlak yollar (sensor_script.py'deki ile aynı olmalı)
LOCK_FILE_PATH_FOR_DASH = '/tmp/sensor_scan_script.lock'
PID_FILE_PATH_FOR_DASH = '/tmp/sensor_scan_script.pid'

app = DjangoDash('RealtimeSensorDashboard', external_stylesheets=[dbc.themes.BOOTSTRAP])

app.layout = dbc.Container(fluid=True, children=[  # dbc.Container ve fluid=True
    html.H1("Eş Zamanlı Servo Motorlu 2D Alan Tarama Paneli", className="text-center my-4"),  # Bootstrap class'ları

    dbc.Row([  # Buton ve durum mesajı için satır
        dbc.Col([
            html.Button('2D Taramayı Başlat', id='start-scan-button', n_clicks=0,
                        className="btn btn-success btn-lg me-2"),  # Bootstrap class'ları ve margin
            html.Span(id='scan-status-message', style={'fontSize': '16px'})
        ], className="text-center mb-4"),

        dbc.Col(dcc.Graph(id='scan-map-graph'), md=12)
    ]),

    dcc.Interval(
        id='interval-component-scan',
        interval=1200,
        n_intervals=0
    ),

    dbc.Row([  # Özet için satır
        dbc.Col(html.Div(id='scan-summary-realtime',
                         style={'padding': '20px', 'fontSize': '16px', 'marginTop': '20px',
                                'border': '1px solid #ddd', 'borderRadius': '5px',
                                'backgroundColor': '#f9f9f9'}),
                width=12)
    ])
])


def is_process_running(pid):
    """Verilen PID ile bir process'in çalışıp çalışmadığını kontrol eder (Linux)."""
    if pid is None:
        return False
    try:
        os.kill(pid, 0)  # Sinyal göndermeden process var mı diye kontrol et
    except OSError:  # No such process
        return False
    else:  # No error, process exists
        return True


@app.callback(
    Output('scan-status-message', 'children'),
    [Input('start-scan-button', 'n_clicks')],
    prevent_initial_call=True
)
def handle_start_scan_script(n_clicks):
    ctx = dash.callback_context
    if not ctx.triggered or n_clicks == 0:  # n_clicks=0 kontrolü eklendi
        return dash.no_update

    button_id = ctx.triggered[0]['prop_id'].split('.')[0]
    if button_id == 'start-scan-button':
        current_pid = None
        if os.path.exists(PID_FILE_PATH_FOR_DASH):
            try:
                with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                    pid_str = pf.read().strip()
                    if pid_str:  # PID dosyası boş değilse
                        current_pid = int(pid_str)
            except (FileNotFoundError, ValueError, TypeError) as e:  # Olası hatalar eklendi
                print(f"Dash: PID dosyası ({PID_FILE_PATH_FOR_DASH}) okunamadı veya geçersiz: {e}")
                current_pid = None

        if current_pid and is_process_running(current_pid):
            return f"Sensör betiği zaten çalışıyor (PID: {current_pid})."

        # Kilit dosyası var ama PID yoksa veya process çalışmıyorsa, kalıntı olabilir
        if os.path.exists(LOCK_FILE_PATH_FOR_DASH) and (not current_pid or not is_process_running(current_pid)):
            message = f"Kalıntı kilit/PID dosyası bulundu. Siliniyor ve yeniden başlatma denenecek."
            print("Dash: " + message)
            try:
                if os.path.exists(PID_FILE_PATH_FOR_DASH): os.remove(PID_FILE_PATH_FOR_DASH)
                if os.path.exists(LOCK_FILE_PATH_FOR_DASH): os.remove(LOCK_FILE_PATH_FOR_DASH)
            except OSError as e_rm_lock:
                return f"Kalıntı kilit/PID dosyası silinirken hata: {e_rm_lock}. Lütfen manuel kontrol edin: {LOCK_FILE_PATH_FOR_DASH}, {PID_FILE_PATH_FOR_DASH}"

        try:
            python_executable = sys.executable

            if not os.path.exists(SENSOR_SCRIPT_PATH):
                return f"HATA: Sensör betiği bulunamadı: {SENSOR_SCRIPT_PATH}. Lütfen Django projenizin ana dizininde olduğundan emin olun."

            print(f"Dash: '{SENSOR_SCRIPT_PATH}' betiği '{python_executable}' ile başlatılıyor...")


            process = subprocess.Popen(
                [python_executable, SENSOR_SCRIPT_PATH],
                start_new_session=True,
            )

            time.sleep(2.5)

            if os.path.exists(PID_FILE_PATH_FOR_DASH):
                new_pid = None

                try:
                    with open(PID_FILE_PATH_FOR_DASH, 'r') as pf_new:

                        pid_str_new = pf_new.read().strip()
                        if pid_str_new: new_pid = int(pid_str_new)

                    if new_pid and is_process_running(new_pid):
                        return f"Sensör okuması başlatıldı. ID : {new_pid}"
                    else:
                        return f"Sensör betiği başlatıldı ancak PID ({new_pid}) ile çalışan bir process bulunamadı veya PID dosyası boş/hatalı."
                except Exception as e_pid_read:
                    return f"Sensör betiği başlatıldı ancak PID okunamadı ({PID_FILE_PATH_FOR_DASH}): {e_pid_read}"
            else:
                # process.poll() ile betiğin hemen sonlanıp sonlanmadığını kontrol edebilirsiniz
                # poll_result = process.poll()
                # if poll_result is not None:
                #     stdout, stderr = process.communicate()
                #     error_output = stderr.decode(errors='ignore') if stderr else "Bilinmeyen hata (stdout/stderr yok)"
                #     return f"Sensör betiği başlatıldı ancak hemen sonlandı (çıkış kodu: {poll_result}). Hata: {error_output}"
                return f"Sensör betiği başlatma komutu gönderildi, ancak PID dosyası ({PID_FILE_PATH_FOR_DASH}) oluşmadı. Betik loglarını veya Raspberry Pi konsolunu kontrol edin."

        except FileNotFoundError:
            return f"HATA: Python yorumlayıcısı ({sys.executable}) veya sensör betiği ({SENSOR_SCRIPT_PATH}) bulunamadı!"
        except Exception as e:
            return f"Sensör betiği başlatılırken genel hata: {str(e)}"
    return dash.no_update


@app.callback(
    [Output('scan-map-graph', 'figure'),
     Output('scan-summary-realtime', 'children')],
    [Input('interval-component-scan', 'n_intervals')]
)
def update_scan_map_graph(n):
    conn = None
    df_points = pd.DataFrame()
    error_message_div = []
    latest_scan_id = None
    latest_scan_status = "Bilinmiyor"
    latest_scan_start_time_str = "N/A"

    fig_map = go.Figure()
    summary_children = [html.P("Veri bekleniyor...")]

    try:
        if not os.path.exists(DB_PATH):
            # Bu hata mesajı, dosya gerçekten yoksa gösterilir.
            # Eğer dosya var ama kilitliyse, OperationalError daha olasıdır.
            raise FileNotFoundError(
                f"Veritabanı dosyası bulunamadı: {DB_PATH}. Sensör betiği çalıştırıldı ve veritabanını oluşturdu mu?")

        conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, timeout=5)  # Salt okunur modda

        df_latest_scan_info = pd.read_sql_query(
            "SELECT id, status, start_time FROM servo_scans ORDER BY start_time DESC LIMIT 1", conn
        )

        if not df_latest_scan_info.empty:
            latest_scan_id = int(df_latest_scan_info['id'].iloc[0])
            latest_scan_status = df_latest_scan_info['status'].iloc[0]
            latest_scan_start_epoch = df_latest_scan_info['start_time'].iloc[0]
            latest_scan_start_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(latest_scan_start_epoch))

            # Sadece 'running' veya 'completed' durumundaki taramaların noktalarını çekmek daha iyi olabilir.
            # Veya her zaman en son ID'nin noktalarını çek.
            df_points = pd.read_sql_query(
                f"SELECT angle_deg, mesafe_cm FROM scan_points WHERE scan_id = {latest_scan_id} ORDER BY angle_deg ASC",
                conn)
        else:
            # Veritabanında hiç tarama kaydı yoksa
            error_message_div = [
                html.P(f"Veritabanında ({DB_PATH}) hiç tarama kaydı bulunamadı.", style={'color': 'orange'})]


    except sqlite3.OperationalError as e_sql:
        msg = f"Veritabanı okuma hatası: {e_sql}. Veritabanı kilitli olabilir veya dosya bozuk. Sensör betiği çalışıyor mu?"
        print("Dash Update Error (OperationalError):", msg)  # Konsola logla
        error_message_div = [html.P(msg, style={'color': 'red'})]
    except FileNotFoundError as e_fnf:
        msg = str(e_fnf)
        print("Dash Update Error (FileNotFoundError):", msg)
        error_message_div = [html.P(msg, style={'color': 'orange'})]
    except Exception as e_gen:
        msg = f"Veri okunurken bilinmeyen bir hata: {e_gen}"
        print("Dash Update Error (General Exception):", msg)
        error_message_div = [html.P(msg, style={'color': 'red'})]
    finally:
        if conn:
            conn.close()

    if error_message_div:  # Hata varsa, hata mesajını ve boş bir grafik göster
        summary_children = error_message_div
        fig_map.update_layout(title_text='2D Tarama Haritası (Veri Yüklenemedi/Hata)')
    elif not df_points.empty:
        # Sensörün maksimum menzilini (sensor_script.py'deki max_distance * 100) burada da kullanmak iyi olur.
        # Şimdilik 200cm (2.0m) olarak varsayalım veya daha dinamik hale getirin.
        max_plot_distance = 200.0
        df_points_valid = df_points[
            (df_points['mesafe_cm'] > 0.1) & (df_points['mesafe_cm'] < max_plot_distance)
            ].copy()  # SettingWithCopyWarning'den kaçınmak için .copy()

        if not df_points_valid.empty:
            # Açıları radyana çevir
            df_points_valid.loc[:, 'angle_rad'] = np.radians(df_points_valid['angle_deg'])
            # Kutupsal koordinatları Kartezyen'e çevir
            # Sensörün 0 derecesi X ekseninin pozitif yönü (ileri) ise:
            # y = mesafe * sin(açı) (yatay yayılım)
            # x = mesafe * cos(açı) (ileri mesafe)
            df_points_valid.loc[:, 'x_coord'] = df_points_valid['mesafe_cm'] * np.cos(df_points_valid['angle_rad'])
            df_points_valid.loc[:, 'y_coord'] = df_points_valid['mesafe_cm'] * np.sin(df_points_valid['angle_rad'])

            fig_map.add_trace(go.Scatter(
                x=df_points_valid['y_coord'],
                y=df_points_valid['x_coord'],
                mode='markers',
                name='Engeller',
                marker=dict(
                    size=5,
                    color=df_points_valid['mesafe_cm'],
                    colorscale='Viridis',  # Farklı bir renk skalası deneyebilirsiniz: Plasma, Jet, Bluered vb.
                    showscale=True,
                    colorbar_title_text="Mesafe (cm)"
                )
            ))
            # Sensörün konumunu (0,0) olarak işaretle
            fig_map.add_trace(go.Scatter(
                x=[0], y=[0], mode='markers',
                marker=dict(size=10, symbol='diamond', color='red'), name='Sensör Konumu'
            ))

            fig_map.update_layout(
                title_text=f'Canlı 2D Tarama Haritası (ID: {latest_scan_id}, Başlangıç: {latest_scan_start_time_str}, Durum: {latest_scan_status})',
                xaxis_title="Yatay Yayılım (cm)",
                yaxis_title="İleri Mesafe (cm)",
                yaxis_scaleanchor="x",  # Eksenleri eşit ölçeklendir
                yaxis_scaleratio=1,
                # yaxis_autorange='reversed', # İleri yönü yukarıda göstermek için (isteğe bağlı)
                width=700, height=650,  # Grafik boyutları
                margin=dict(l=40, r=40, t=70, b=40),
                plot_bgcolor='rgba(248,248,248,1)'
            )
        else:
            fig_map.update_layout(
                title_text=f'2D Tarama (ID: {latest_scan_id}, Durum: {latest_scan_status} - Çizilecek geçerli nokta yok)')

        # Özet Bilgiler
        summary_children = [html.H4("Tarama Özeti:", style={'marginTop': '0px', 'marginBottom': '10px'})]
        if latest_scan_id is not None:  # latest_scan_id'nin None olup olmadığını kontrol et
            summary_children.append(html.P(
                f"Aktif/Son Tarama ID: {latest_scan_id} (Başlangıç: {latest_scan_start_time_str}, Durum: {latest_scan_status})"))
        summary_children.append(html.P(f"Toplam Okunan Nokta Sayısı (Bu Tarama): {len(df_points)}"))
        if not df_points_valid.empty:
            summary_children.append(html.P(f"Grafiğe Çizilen Geçerli Nokta Sayısı: {len(df_points_valid)}"))
            summary_children.append(html.P(f"Min Algılanan Mesafe: {df_points_valid['mesafe_cm'].min():.2f} cm"))
            summary_children.append(html.P(f"Max Algılanan Mesafe: {df_points_valid['mesafe_cm'].max():.2f} cm"))
    else:  # df_points boşsa (hata yok ama veri de yok)
        fig_map.update_layout(title_text='2D Tarama Haritası (Veri Bekleniyor)')
        if latest_scan_id is not None:
            summary_children = [html.P(
                f"Tarama ID: {latest_scan_id} (Durum: {latest_scan_status}). Bu tarama için henüz nokta bulunamadı veya veritabanı boş.")]
        else:
            summary_children = [html.P("Aktif tarama veya görüntülenecek veri yok. Lütfen taramayı başlatın.")]

    return fig_map, summary_children
