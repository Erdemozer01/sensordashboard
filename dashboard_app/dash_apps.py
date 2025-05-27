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

app.layout = html.Div(
    children=[
        dbc.Row([
            dbc.Col(html.H1("Eş Zamanlı Servo Motorlu 2D Alan Tarama Paneli",
                            className="text-center my-3"),  # my-3: üst ve alt margin
                    width=12)
        ]),

        dbc.Row([
            # --- Sol Kolon (Başlatıcı ve Özet) ---
            dbc.Col([
                html.Div([
                    html.Button('2D Taramayı Başlat',
                                id='start-scan-button',
                                n_clicks=0,
                                className="btn btn-success btn-lg w-100 mb-3"),
                    # w-100: kolonun tamamını kapla, mb-3: alt margin
                ]),
                html.Div(
                    html.Span(id='scan-status-message',
                              style={'fontSize': '16px', 'display': 'block', 'marginBottom': '20px',
                                     'minHeight': '20px'}),
                    # minHeight eklendi
                    className="text-center"
                ),
                html.Div(
                    id='scan-summary-realtime',
                    style={'padding': '15px', 'fontSize': '15px',
                           'border': '1px solid #ddd', 'borderRadius': '5px',
                           'backgroundColor': '#f9f9f9', 'minHeight': '150px'}  # minHeight eklendi
                )
            ], md=4),  # Orta ve büyük ekranlarda 4 kolon, küçük ekranlarda alta geçer

            # --- Sağ Kolon (Grafik) ---
            dbc.Col([
                dcc.Graph(id='scan-map-graph', style={'height': '75vh'})  # Yüksekliği viewport'a göre ayarla
            ], md=8)  # Orta ve büyük ekranlarda 8 kolon
        ]),

        dcc.Interval(
            id='interval-component-scan',
            interval=1200,  # Her 1.2 saniyede bir güncelle
            n_intervals=0
        )
    ])


def is_process_running(pid):

    if pid is None:
        return False
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
                    if pid_str:
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
    [Output('scan-map-graph', 'figure'),  # 2D Harita grafiği için
     Output('scan-summary-realtime', 'children')],  # Özet bilgiler için
    [Input('interval-component-scan', 'n_intervals')]  # Periyodik güncelleme için interval
)
def update_scan_map_graph(n_intervals):  # n_intervals parametresi callback tarafından sağlanır
    conn = None
    df_points = pd.DataFrame()  # Başlangıçta boş DataFrame
    error_message_div = []  # Olası hata mesajlarını tutmak için
    latest_scan_id = None
    latest_scan_status = "Bilinmiyor"
    latest_scan_start_time_str = "N/A"

    fig_map = go.Figure()  # Boş bir figür oluştur
    summary_children = [html.P("Veri bekleniyor...")]  # Varsayılan özet mesajı

    try:
        # Veritabanı dosyasının varlığını kontrol et
        if not os.path.exists(DB_PATH):
            raise FileNotFoundError(
                f"Veritabanı dosyası bulunamadı: {DB_PATH}. Sensör betiği çalıştırıldı ve veritabanını oluşturdu mu?")

        # Veritabanına salt okunur modda bağlanmayı dene (daha güvenli)
        conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, timeout=5)

        # En son taramayı (önce 'running' durumundakini, yoksa en sonuncusunu) bul
        df_latest_scan_info = pd.read_sql_query(
            "SELECT id, status, start_time FROM servo_scans WHERE status = 'running' ORDER BY start_time DESC LIMIT 1",
            conn
        )
        if df_latest_scan_info.empty:  # 'running' durumda tarama yoksa, en son tamamlanmış/kesilmiş taramayı al
            df_latest_scan_info = pd.read_sql_query(
                "SELECT id, status, start_time FROM servo_scans ORDER BY start_time DESC LIMIT 1", conn
            )

        if not df_latest_scan_info.empty:
            latest_scan_id = int(df_latest_scan_info['id'].iloc[0])
            latest_scan_status = df_latest_scan_info['status'].iloc[0]
            latest_scan_start_epoch = df_latest_scan_info['start_time'].iloc[0]
            latest_scan_start_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(latest_scan_start_epoch))

            # İlgili tarama ID'sine ait tüm noktaları çek
            df_points = pd.read_sql_query(
                f"SELECT angle_deg, mesafe_cm FROM scan_points WHERE scan_id = {latest_scan_id} ORDER BY angle_deg ASC",
                conn
            )
        else:
            # Veritabanında hiç tarama kaydı yoksa
            error_message_div = [
                html.P(f"Veritabanında ({DB_PATH}) hiç tarama kaydı bulunamadı.", style={'color': 'orange'})]

    except sqlite3.OperationalError as e_sql:
        msg = f"Veritabanı okuma hatası: {e_sql}. Veritabanı kilitli olabilir veya dosya bozuk. Sensör betiği çalışıyor mu?"
        print("Dash Update Error (OperationalError):", msg)  # Sunucu konsoluna logla
        error_message_div = [html.P(msg, style={'color': 'red'})]
    except FileNotFoundError as e_fnf:
        msg = str(e_fnf)
        print("Dash Update Error (FileNotFoundError):", msg)
        error_message_div = [html.P(msg, style={'color': 'orange'})]
    except Exception as e_gen:  # Diğer genel hatalar
        msg = f"Veri okunurken bilinmeyen bir hata: {e_gen}"
        print("Dash Update Error (General Exception):", msg)
        error_message_div = [html.P(msg, style={'color': 'red'})]
    finally:
        if conn:
            conn.close()

    # Hata oluştuysa, hata mesajını ve boş bir grafik göster
    if error_message_div:
        summary_children = error_message_div
        fig_map.update_layout(
            title_text='2D Tarama Haritası (Veri Yüklenemedi/Hata)',
            xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),  # Temiz bir görünüm için eksenleri gizle
            yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
            plot_bgcolor='rgba(240,240,240,0.95)'
        )
    elif not df_points.empty:  # Hata yok ve veri varsa grafiği oluştur
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

            # Saçılım grafiğini oluştur (engel noktaları)
            fig_map.add_trace(go.Scatter(
                x=df_points_valid['y_coord'],  # Yatay eksende y_coord (sinüs bileşeni)
                y=df_points_valid['x_coord'],  # Dikey eksende x_coord (kosinüs bileşeni - ileri yön)
                mode='markers',
                name='Algılanan Engeller',
                marker=dict(
                    size=6,
                    color=df_points_valid['mesafe_cm'],
                    colorscale='Plasma',  # Renk skalası: Viridis, Plasma, Jet, Bluered vb.
                    showscale=True,
                    colorbar_title_text="Mesafe (cm)",
                    colorbar_x=1.15,  # Renk skalasının konumu (grafiğin sağına)
                    colorbar_thickness=15,
                    colorbar_len=0.85
                )
            ))
            # Sensörün konumunu (0,0) olarak işaretle
            fig_map.add_trace(go.Scatter(
                x=[0], y=[0], mode='markers',
                marker=dict(size=12, symbol='diamond', color='red'), name='Sensör Konumu'
            ))

            fig_map.update_layout(
                title_text=f'Canlı 2D Tarama Haritası (ID: {latest_scan_id}, Başlangıç: {latest_scan_start_time_str}, Durum: {latest_scan_status})',
                xaxis_title="Yatay Yayılım (cm)",
                yaxis_title="İleri Mesafe (cm)",
                yaxis_scaleanchor="x",  # Eksenleri eşit ölçeklendir (gerçekçi görünüm için)
                yaxis_scaleratio=1,
                # yaxis_autorange='reversed', # İleri yönü yukarıda göstermek için (isteğe bağlı)
                width=None,  # Konteynerin genişliğine uysun
                height=650,  # Grafik yüksekliği
                margin=dict(l=50, r=50, b=50, t=80),  # Kenar boşlukları
                plot_bgcolor='rgba(248,248_248,1)',  # Grafik arka plan rengi
                legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01)  # Legend konumu
            )
        else:  # Geçerli nokta yoksa
            fig_map.update_layout(
                title_text=f'2D Tarama (ID: {latest_scan_id}, Durum: {latest_scan_status} - Çizilecek geçerli nokta yok)')

        # Özet Bilgiler
        summary_children = [html.H4("Tarama Özeti:", style={'marginTop': '0px', 'marginBottom': '10px'})]
        if latest_scan_id is not None:
            summary_children.append(html.P(
                f"Aktif/Son Tarama ID: {latest_scan_id} (Başlangıç: {latest_scan_start_time_str}, Durum: {latest_scan_status})"))
        summary_children.append(html.P(f"Toplam Okunan Nokta Sayısı (DB'de): {len(df_points)}"))
        if not df_points_valid.empty:
            summary_children.append(html.P(f"Grafiğe Çizilen Geçerli Nokta Sayısı: {len(df_points_valid)}"))
            summary_children.append(html.P(f"Min Algılanan Mesafe: {df_points_valid['mesafe_cm'].min():.2f} cm"))
            summary_children.append(html.P(f"Max Algılanan Mesafe: {df_points_valid['mesafe_cm'].max():.2f} cm"))
    else:  # df_points boşsa (hata yok ama veri de yok)
        fig_map.update_layout(title_text='2D Tarama Haritası (Veri Bekleniyor)')
        if latest_scan_id is not None:  # En azından bir tarama denemesi olmuşsa
            summary_children = [html.P(
                f"Tarama ID: {latest_scan_id} (Durum: {latest_scan_status}). Bu tarama için henüz nokta bulunamadı veya veritabanı boş.")]
        else:  # Hiç tarama kaydı yoksa
            summary_children = [html.P("Aktif tarama veya görüntülenecek veri yok. Lütfen taramayı başlatın.")]

    return fig_map, summary_children
