
import os
import sqlite3

import dash
import plotly.graph_objects as go
import dash_bootstrap_components as dbc
from dash import html, dcc, Output, Input, State
from django_plotly_dash import DjangoDash

import time
import pandas as pd
import numpy as np
import json
import sys
import signal
import subprocess
import io

# --- Sabitler ---
PROJECT_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILENAME = 'live_scan_data.sqlite3'
DB_PATH = os.path.join(PROJECT_ROOT_DIR, DB_FILENAME)
SENSOR_SCRIPT_FILENAME = 'sensor_script.py'
SENSOR_SCRIPT_PATH = os.path.join(PROJECT_ROOT_DIR, SENSOR_SCRIPT_FILENAME)
LOCK_FILE_PATH_FOR_DASH = '/tmp/sensor_scan_script.lock'
PID_FILE_PATH_FOR_DASH = '/tmp/sensor_scan_script.pid'

def is_process_running(pid):
    if pid is None: return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    else:
        return True

app = DjangoDash("RealtimeSensorDashboard", external_stylesheets=[dbc.themes.BOOTSTRAP],)


# Anlık sensör değerlerini gösteren bir panel ekleyin
stats_panel = dbc.Card([
    dbc.CardHeader("Anlık Sensör Değerleri", className="bg-primary text-white"),
    dbc.CardBody([
        html.Div(id='realtime-values', children=[
            dbc.Row([
                dbc.Col(html.Div([
                    html.H5("Mevcut Açı:"),
                    html.H3(id='current-angle', children="--°", className="text-info")
                ]), width=4),
                dbc.Col(html.Div([
                    html.H5("Mevcut Mesafe:"),
                    html.H3(id='current-distance', children="-- cm", className="text-success")
                ]), width=4),
                dbc.Col(html.Div([
                    html.H5("Hız:"),
                    html.H3(id='current-speed', children="-- cm/s", className="text-warning")
                ]), width=4)
            ])
        ])
    ])
])

# Tarama kontrolü için daha kapsamlı bir panel
control_panel = dbc.Card([
    dbc.CardHeader("Tarama Kontrolü", className="bg-secondary text-white"),
    dbc.CardBody([
        dbc.Row([
            dbc.Col([
                html.Button('2D Taramayı Başlat', id='start-scan-button', 
                           className="btn btn-success btn-lg w-100 mb-3"),
                html.Button('Taramayı Durdur', id='stop-scan-button', 
                           className="btn btn-danger btn-lg w-100 mb-3"),
            ], width=12)
        ]),
        dbc.Row([
            dbc.Col([
                html.H6("Tarama Ayarları:"),
                dbc.InputGroup([
                    dbc.InputGroupText("Başlangıç Açısı"),
                    dbc.Input(id="start-angle-input", type="number", value=0, min=0, max=180)
                ], className="mb-2"),
                dbc.InputGroup([
                    dbc.InputGroupText("Bitiş Açısı"),
                    dbc.Input(id="end-angle-input", type="number", value=180, min=0, max=180)
                ], className="mb-2"),
                dbc.InputGroup([
                    dbc.InputGroupText("Adım Sayısı"),
                    dbc.Input(id="step-angle-input", type="number", value=10, min=1, max=45)
                ], className="mb-2")
            ], width=12)
        ])
    ])
])

# Çoklu görselleştirme seçenekleri
visualization_tabs = dbc.Tabs([
    dbc.Tab([
        dcc.Graph(id='scan-map-graph', style={'height': '70vh'})
    ], label="2D Harita"),
    dbc.Tab([
        dcc.Graph(id='polar-graph', style={'height': '70vh'})
    ], label="Polar Grafik"),
    dbc.Tab([
        dcc.Graph(id='time-series-graph', style={'height': '70vh'})
    ], label="Zaman Serisi")
])

# Tarama seçici
scan_selector = dbc.Card([
    dbc.CardHeader("Tarama Karşılaştırma", className="bg-info text-white"),
    dbc.CardBody([
        dbc.Row([
            dbc.Col([
                html.Label("Birincil Tarama:"),
                dcc.Dropdown(id='primary-scan-dropdown', placeholder="Tarama seçin...")
            ], width=6),
            dbc.Col([
                html.Label("İkincil Tarama:"),
                dcc.Dropdown(id='secondary-scan-dropdown', placeholder="Karşılaştırma için tarama seçin...")
            ], width=6)
        ]),
        dbc.Row([
            dbc.Col([
                html.Button('Karşılaştır', id='compare-button', className="btn btn-info mt-2")
            ], width=12)
        ])
    ])
])

# Sistem durum kartı
system_card = dbc.Card([
    dbc.CardHeader("Sistem Durumu", className="bg-warning text-dark"),
    dbc.CardBody([
        dbc.Row([
            dbc.Col([
                html.Div([
                    html.H5("Sensör Betiği:"),
                    html.H4(id='script-status', children="Beklemede", className="text-secondary")
                ])
            ], width=6),
            dbc.Col([
                html.Div([
                    html.H5("Servo Pozisyonu:"),
                    html.H4(id='servo-position', children="--°", className="text-secondary")
                ])
            ], width=6)
        ]),
        dbc.Row([
            dbc.Col([
                html.Div([
                    html.H5("Raspberry Pi CPU:"),
                    dbc.Progress(id='cpu-usage', value=0, color="success", className="mb-3")
                ])
            ], width=6),
            dbc.Col([
                html.Div([
                    html.H5("Raspberry Pi RAM:"),
                    dbc.Progress(id='ram-usage', value=0, color="info", className="mb-3")
                ])
            ], width=6)
        ])
    ])
])

# Dışa aktarma seçenekleri
export_card = dbc.Card([
    dbc.CardHeader("Veri Dışa Aktarma", className="bg-success text-white"),
    dbc.CardBody([
        dbc.Row([
            dbc.Col([
                html.Button('CSV Olarak İndir', id='export-csv-button', className="btn btn-outline-success mr-2"),
                html.Button('Excel Olarak İndir', id='export-excel-button', className="btn btn-outline-success mr-2"),
                html.Button('Grafiği Kaydet (PNG)', id='save-image-button', className="btn btn-outline-success")
            ], width=12)
        ])
    ])
])

analysis_card = dbc.Card([
    dbc.CardHeader("Tarama Analizi", className="bg-dark text-white"),
    dbc.CardBody([
        html.Div(id='analysis-output', children=[
            dbc.Row([
                dbc.Col([
                    html.H5("Hesaplanan Alan:"),
                    html.H3(id='calculated-area', children="-- cm²", className="text-primary")
                ], width=6),
                dbc.Col([
                    html.H5("Çevre Uzunluğu:"),
                    html.H3(id='perimeter-length', children="-- cm", className="text-primary")
                ], width=6)
            ]),
            dbc.Row([
                dbc.Col([
                    html.H5("Max Genişlik:"),
                    html.H3(id='max-width', children="-- cm", className="text-info")
                ], width=6),
                dbc.Col([
                    html.H5("Max Derinlik:"),
                    html.H3(id='max-depth', children="-- cm", className="text-info")
                ], width=6)
            ])
        ])
    ])
])


app.layout = dbc.Container(fluid=True, children=[
    dbc.Row([
        dbc.Col(html.H1("Dream Pi - 2D Alan Tarama Sistemi", className="text-center my-3"), width=12)
    ]),

    dbc.Row([
        # Sol Kolon - Kontrol Panelleri
        dbc.Col([
            control_panel,
            html.Div(style={"height": "20px"}),
            stats_panel,
            html.Div(style={"height": "20px"}),
            system_card,
            html.Div(style={"height": "20px"}),
            scan_selector,
            html.Div(style={"height": "20px"}),
            export_card
        ], md=3),

        # Orta ve Sağ Kolon - Görselleştirme ve Analiz
        dbc.Col([
            dbc.Row([
                dbc.Col([
                    visualization_tabs
                ], width=12)
            ]),
            dbc.Row([
                dbc.Col([
                    analysis_card
                ], width=12)
            ]),
        ], md=9)
    ]),

    # Gizli bileşenler
    dcc.Interval(id='interval-component-scan', interval=1500, n_intervals=0),
    dcc.Interval(id='interval-component-system', interval=5000, n_intervals=0),
    dcc.Download(id='download-csv'),
    dcc.Download(id='download-excel'),
    html.Div(id='current-scan-id', style={'display': 'none'}, children="1"),
    html.Div(id='scan-status-message', style={'marginTop': '10px', 'color': 'blue'}),
    dcc.Download(id='download-image'),
])


# Tarama listesini güncellemek için callback
@app.callback(
    [Output('primary-scan-dropdown', 'options'),
     Output('secondary-scan-dropdown', 'options')],
    [Input('interval-component-scan', 'n_intervals')]
)
def update_scan_dropdowns(n_intervals):
    conn = None
    options = []

    try:
        conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
        df = pd.read_sql_query(
            "SELECT id, start_time, status FROM servo_scans ORDER BY start_time DESC LIMIT 20", conn
        )

        for index, row in df.iterrows():
            scan_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(row['start_time']))
            label = f"ID: {row['id']} - {scan_time} - {row['status']}"
            options.append({"label": label, "value": row['id']})
    except Exception as e:
        print(f"Tarama listesi güncellenirken hata: {e}")
    finally:
        if conn: conn.close()

    return options, options


# CSV dışa aktarma callback'i
@app.callback(
    Output('download-csv', 'data'),
    [Input('export-csv-button', 'n_clicks')],
    [State('primary-scan-dropdown', 'value')],
    prevent_initial_call=True
)
def export_csv(n_clicks, scan_id):
    if not scan_id:
        return dash.no_update

    conn = None
    try:
        conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
        df = pd.read_sql_query(
            f"SELECT angle_deg, mesafe_cm, hiz_cm_s, x_cm, y_cm, timestamp FROM scan_points WHERE scan_id = {scan_id}",
            conn
        )
        return dcc.send_data_frame(df.to_csv, f"tarama_{scan_id}.csv", index=False)
    except Exception as e:
        print(f"CSV dışa aktarma hatası: {e}")
        return dash.no_update
    finally:
        if conn: conn.close()


# Alan analizi güncelleme callback'i
@app.callback(
    [Output('calculated-area', 'children'),
     Output('perimeter-length', 'children'),
     Output('max-width', 'children'),
     Output('max-depth', 'children')],
    [Input('interval-component-scan', 'n_intervals'),
     Input('primary-scan-dropdown', 'value')]
)
def update_analysis(n_intervals, selected_scan_id):
    conn = None
    try:
        # Eğer seçilen bir tarama yoksa, en son taramayı kullan
        if not selected_scan_id:
            conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
            df_last = pd.read_sql_query(
                "SELECT id FROM servo_scans ORDER BY start_time DESC LIMIT 1", conn
            )
            if not df_last.empty:
                selected_scan_id = int(df_last['id'].iloc[0])
            else:
                return "-- cm²", "-- cm", "-- cm", "-- cm"

        # Seçilen taramanın verilerini al
        conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
        df = pd.read_sql_query(
            f"SELECT x_cm, y_cm FROM scan_points WHERE scan_id = {selected_scan_id}", conn
        )

        if df.empty:
            return "-- cm²", "-- cm", "-- cm", "-- cm"

        # Alan hesaplama (varsa hesaplanan_alan_cm2 değerini kullan)
        df_scan = pd.read_sql_query(
            f"SELECT hesaplanan_alan_cm2 FROM servo_scans WHERE id = {selected_scan_id}", conn
        )

        area_value = "-- cm²"
        if not df_scan.empty and df_scan['hesaplanan_alan_cm2'].iloc[0] is not None:
            area_value = f"{df_scan['hesaplanan_alan_cm2'].iloc[0]:.2f} cm²"

        # Çevre uzunluğu (noktalar arasındaki mesafeyi topla)
        perimeter = 0
        if len(df) > 1:
            for i in range(len(df) - 1):
                dx = df['x_cm'].iloc[i + 1] - df['x_cm'].iloc[i]
                dy = df['y_cm'].iloc[i + 1] - df['y_cm'].iloc[i]
                perimeter += np.sqrt(dx ** 2 + dy ** 2)

            # İlk ve son noktaları da bağla (opsiyonel)
            dx = df['x_cm'].iloc[0] - df['x_cm'].iloc[-1]
            dy = df['y_cm'].iloc[0] - df['y_cm'].iloc[-1]
            perimeter += np.sqrt(dx ** 2 + dy ** 2)

        # Maksimum genişlik ve derinlik
        width = df['y_cm'].max() - df['y_cm'].min() if len(df) > 0 else 0
        depth = df['x_cm'].max() if len(df) > 0 else 0

        return area_value, f"{perimeter:.2f} cm", f"{width:.2f} cm", f"{depth:.2f} cm"
    except Exception as e:
        print(f"Analiz güncelleme hatası: {e}")
        return "Hata", "Hata", "Hata", "Hata"
    finally:
        if conn: conn.close()


# Sistem durumu güncelleme callback'i
@app.callback(
    [Output('script-status', 'children'),
     Output('script-status', 'className'),
     Output('servo-position', 'children')],
    [Input('interval-component-scan', 'n_intervals')]
)
def update_system_status(n_intervals):
    # Sensör betik durumunu kontrol et
    current_pid = None
    if os.path.exists(PID_FILE_PATH_FOR_DASH):
        try:
            with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                pid_str = pf.read().strip()
                if pid_str:
                    current_pid = int(pid_str)
        except:
            current_pid = None

    if current_pid and is_process_running(current_pid):
        script_status = "Çalışıyor"
        status_class = "text-success"
    else:
        script_status = "Beklemede"
        status_class = "text-secondary"

    # Son servo pozisyonunu al
    servo_position = "--°"
    conn = None
    try:
        conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
        df = pd.read_sql_query(
            "SELECT angle_deg FROM scan_points ORDER BY id DESC LIMIT 1", conn
        )
        if not df.empty:
            servo_position = f"{df['angle_deg'].iloc[0]:.1f}°"
    except Exception as e:
        print(f"Servo pozisyonu alınırken hata: {e}")
    finally:
        if conn: conn.close()

    return script_status, status_class, servo_position

@app.callback(
    [Output('cpu-usage', 'value'),
     Output('ram-usage', 'value')],
    [Input('interval-component-system', 'n_intervals')]
)
def update_system_metrics(n_intervals):
    try:
        # CPU kullanımı
        cpu_percent = 0
        
        # Linux sistemlerinde CPU kullanımı
        if os.path.exists('/proc/stat'):
            with open('/proc/stat', 'r') as f:
                cpu_line = f.readline().split()
                user = float(cpu_line[1])
                nice = float(cpu_line[2])
                system = float(cpu_line[3])
                idle = float(cpu_line[4])
                total = user + nice + system + idle
                cpu_percent = 100 * (1 - idle / total)
        
        # RAM kullanımı
        mem_percent = 0
        
        # Linux sistemlerinde RAM kullanımı
        if os.path.exists('/proc/meminfo'):
            mem_info = {}
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    key, value = line.split(':')
                    value = value.strip()
                    if value.endswith('kB'):
                        value = float(value[:-2]) * 1024
                    mem_info[key] = value
            
            if 'MemTotal' in mem_info and 'MemFree' in mem_info:
                mem_total = float(mem_info['MemTotal'])
                mem_free = float(mem_info['MemFree'])
                mem_percent = 100 * (1 - mem_free / mem_total)
        
        return min(100, max(0, cpu_percent)), min(100, max(0, mem_percent))
    except Exception as e:
        print(f"Sistem metrikleri alınırken hata: {e}")
        return 0, 0

@app.callback(
    Output('scan-map-graph', 'figure'),
    [Input('interval-component-scan', 'n_intervals')],
    [State('primary-scan-dropdown', 'value')],
    prevent_initial_call=True
)
def update_scan_map_graph(n_intervals, selected_scan_id):
    # Karşılaştırma butonu basıldığında çalışmamasını sağla
    ctx = dash.callback_context
    if ctx.triggered and ctx.triggered[0]['prop_id'] == 'compare-button.n_clicks':
        return dash.no_update
    
    conn = None
    fig_map = go.Figure()
    
    try:
        if not os.path.exists(DB_PATH):
            raise FileNotFoundError(f"Veritabanı dosyası bulunamadı.")

        conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, timeout=5)
        
        # Seçilen bir tarama yoksa, en son taramayı kullan
        if not selected_scan_id:
            df_scan_info = pd.read_sql_query(
                "SELECT id FROM servo_scans ORDER BY start_time DESC LIMIT 1", conn
            )
            if not df_scan_info.empty:
                selected_scan_id = int(df_scan_info['id'].iloc[0])
            else:
                fig_map.update_layout(title_text='2D Tarama Haritası (Tarama Bulunamadı)')
                return fig_map
        
        # Seçilen taramanın verilerini al
        df_points = pd.read_sql_query(
            f"SELECT angle_deg, mesafe_cm, x_cm, y_cm FROM scan_points WHERE scan_id = {selected_scan_id} ORDER BY id ASC",
            conn
        )
        
        if not df_points.empty and 'x_cm' in df_points.columns and 'y_cm' in df_points.columns:
            # Noktaları çizdir
            fig_map.add_trace(go.Scatter(
                x=df_points['y_cm'],
                y=df_points['x_cm'],
                mode='lines+markers',
                name='Taranan Sınır',
                line=dict(color='rgba(0,100,80,0.7)', width=2),
                marker=dict(size=5, color=df_points['mesafe_cm'], colorscale='Viridis', showscale=True,
                           colorbar_title_text="Mesafe (cm)")
            ))
            
            # Alan dolgusu için
            polygon_plot_x = [0] + list(df_points['y_cm'])
            polygon_plot_y = [0] + list(df_points['x_cm'])
            
            fig_map.add_trace(go.Scatter(
                x=polygon_plot_x,
                y=polygon_plot_y,
                fill="toself",
                fillcolor='rgba(0,176,246,0.2)',
                line=dict(color='rgba(255,255,255,0)'),
                hoverinfo="skip",
                showlegend=False,
                name='Taranan Alan'
            ))
            
            # Sensör konumu
            fig_map.add_trace(
                go.Scatter(x=[0], y=[0], mode='markers', marker=dict(size=10, symbol='diamond', color='red'),
                           name='Sensör Konumu')
            )
            
            fig_map.update_layout(
                title_text=f'Alan Tarama #{selected_scan_id}',
                xaxis_title="Yatay Yayılım (cm)",
                yaxis_title="İleri Mesafe (cm)",
                yaxis_scaleanchor="x",
                yaxis_scaleratio=1,
                width=None,
                height=650,
                margin=dict(l=50, r=50, b=50, t=80),
                plot_bgcolor='rgba(248,248,248,1)',
                legend=dict(yanchor="top", y=0.99, xanchor="center", x=0.01)
            )
        else:
            fig_map.update_layout(title_text=f'Çizilecek geçerli nokta yok')
    except Exception as e:
        print(f"Tarama haritası oluşturma hatası: {e}")
        fig_map.update_layout(title_text='2D Tarama Haritası (Hata/Veri Yok)')
    finally:
        if conn: conn.close()
    
    return fig_map

@app.callback(
    Output('scan-status-message', 'children'),
    [Input('start-scan-button', 'n_clicks')],
    [State('start-angle-input', 'value'),
     State('end-angle-input', 'value'),
     State('step-angle-input', 'value')],
    prevent_initial_call=True
)
def handle_start_scan_script(n_clicks, start_angle, end_angle, step_angle):
    ctx = dash.callback_context
    if not ctx.triggered or n_clicks == 0: return dash.no_update
    
    current_pid = None
    if os.path.exists(PID_FILE_PATH_FOR_DASH):
        try:
            with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                pid_str = pf.read().strip()
                if pid_str: current_pid = int(pid_str)
        except:
            current_pid = None
    
    if current_pid and is_process_running(current_pid): 
        return f"Sensör betiği zaten çalışıyor (PID: {current_pid})."
    
    if os.path.exists(LOCK_FILE_PATH_FOR_DASH) and (not current_pid or not is_process_running(current_pid)):
        try:
            if os.path.exists(PID_FILE_PATH_FOR_DASH): os.remove(PID_FILE_PATH_FOR_DASH)
            if os.path.exists(LOCK_FILE_PATH_FOR_DASH): os.remove(LOCK_FILE_PATH_FOR_DASH)
        except OSError as e:
            return f"Kalıntı kilit/PID silinirken hata: {e}."
    
    # Tarama parametrelerini doğrula
    if start_angle is None or end_angle is None or step_angle is None:
        return "Geçersiz tarama parametreleri!"
    
    if start_angle < 0 or start_angle > 180 or end_angle < 0 or end_angle > 180 or step_angle < 1 or step_angle > 45:
        return "Tarama parametreleri geçerli aralıkta değil!"
    
    if start_angle >= end_angle:
        return "Başlangıç açısı, bitiş açısından küçük olmalıdır!"
    
    # Parametreleri dosyaya yaz (sensör betiği tarafından okunabilir)
    try:
        with open('/tmp/sensor_scan_params.json', 'w') as f:
            json.dump({
                'start_angle': start_angle,
                'end_angle': end_angle,
                'step_angle': step_angle
            }, f)
    except Exception as e:
        print(f"Parametre yazma hatası: {e}")
    
    try:
        python_executable = sys.executable
        if not os.path.exists(SENSOR_SCRIPT_PATH): 
            return f"HATA: Sensör betiği bulunamadı: {SENSOR_SCRIPT_PATH}"
        
        process = subprocess.Popen([python_executable, SENSOR_SCRIPT_PATH], start_new_session=True)
        time.sleep(2.5)
        
        if os.path.exists(PID_FILE_PATH_FOR_DASH):
            new_pid = None
            try:
                with open(PID_FILE_PATH_FOR_DASH, 'r') as pf_new:
                    pid_str_new = pf_new.read().strip()
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
    Output('scan-status-message', 'children'),
    [Input('stop-scan-button', 'n_clicks')],
    prevent_initial_call=True
)
def handle_stop_scan_script(n_clicks):
    ctx = dash.callback_context
    if not ctx.triggered or n_clicks == 0:
        return dash.no_update
    
    current_pid = None
    if os.path.exists(PID_FILE_PATH_FOR_DASH):
        try:
            with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                pid_str = pf.read().strip()
                if pid_str:
                    current_pid = int(pid_str)
        except:
            current_pid = None
    
    if current_pid and is_process_running(current_pid):
        try:
            os.kill(current_pid, signal.SIGTERM)
            time.sleep(0.5)
            if is_process_running(current_pid):
                os.kill(current_pid, signal.SIGKILL)
            return "Tarama durduruldu."
        except Exception as e:
            return f"Taramayı durdurma hatası: {e}"
    else:
        return "Durdurulacak aktif tarama bulunamadı."

@app.callback(
    [Output('current-angle', 'children'),
     Output('current-distance', 'children'),
     Output('current-speed', 'children')],
    [Input('interval-component-scan', 'n_intervals')]
)
def update_realtime_values(n_intervals):
    conn = None
    try:
        conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
        df = pd.read_sql_query(
            "SELECT angle_deg, mesafe_cm, hiz_cm_s FROM scan_points ORDER BY id DESC LIMIT 1", 
            conn
        )
        if not df.empty:
            angle = f"{df['angle_deg'].iloc[0]:.1f}°"
            distance = f"{df['mesafe_cm'].iloc[0]:.2f} cm"
            speed = f"{df['hiz_cm_s'].iloc[0]:.2f} cm/s"
            return angle, distance, speed
    except Exception as e:
        print(f"Gerçek zamanlı değerler alınırken hata: {e}")
    finally:
        if conn: conn.close()
    
    return "--°", "-- cm", "-- cm/s"


@app.callback(
    Output('time-series-graph', 'figure'),
    [Input('interval-component-scan', 'n_intervals')],
    [State('primary-scan-dropdown', 'value')]
)
def update_time_series_graph(n_intervals, selected_scan_id):
    conn = None
    fig_time = go.Figure()

    try:
        if not os.path.exists(DB_PATH):
            raise FileNotFoundError(f"Veritabanı dosyası bulunamadı.")

        conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, timeout=5)

        # Seçilen bir tarama yoksa, en son taramayı kullan
        if not selected_scan_id:
            df_scan_info = pd.read_sql_query(
                "SELECT id FROM servo_scans ORDER BY start_time DESC LIMIT 1", conn
            )
            if not df_scan_info.empty:
                selected_scan_id = int(df_scan_info['id'].iloc[0])
            else:
                fig_time.update_layout(title_text='Zaman Serisi Grafiği (Tarama Bulunamadı)')
                return fig_time

        # Seçilen taramanın verilerini al
        df_points = pd.read_sql_query(
            f"SELECT angle_deg, mesafe_cm, timestamp FROM scan_points WHERE scan_id = {selected_scan_id} ORDER BY timestamp ASC",
            conn
        )

        if not df_points.empty:
            # Zaman serisi grafiği çiz
            fig_time.add_trace(go.Scatter(
                x=df_points['timestamp'],
                y=df_points['mesafe_cm'],
                mode='lines+markers',
                name='Mesafe (cm)',
                marker=dict(size=6)
            ))

            # X ekseni için zaman formatı ayarla
            fig_time.update_xaxes(
                title_text="Zaman",
                tickformat="%H:%M:%S",
                tickvals=df_points['timestamp'],
                ticktext=[time.strftime('%H:%M:%S', time.localtime(ts)) for ts in df_points['timestamp']]
            )

            fig_time.update_layout(
                title_text=f'Zaman Serisi Grafiği - Tarama #{selected_scan_id}',
                yaxis_title="Mesafe (cm)",
                height=650,
                margin=dict(l=50, r=50, b=50, t=80),
                plot_bgcolor='rgba(248,248,248,1)'
            )
        else:
            fig_time.update_layout(title_text='Zaman Serisi Grafiği (Veri Bekleniyor)')
    except Exception as e:
        print(f"Zaman serisi grafiği oluşturma hatası: {e}")
        fig_time.update_layout(title_text='Zaman Serisi Grafiği (Hata)')
    finally:
        if conn: conn.close()

    return fig_time
