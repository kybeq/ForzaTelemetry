from flask import Flask, render_template, request, jsonify, url_for, abort
from flask_socketio import SocketIO
import socket
import time
import threading
import logging
import pyautogui
import psutil
import os
import json
import ForzaHorizonTelemetry as FHT
import atexit
import keyboard
import copy
from logging.handlers import RotatingFileHandler
from threading import Lock

app = Flask(__name__)
socketio = SocketIO(app)

handler = RotatingFileHandler('telemetry_dump.log', maxBytes=10*1024*1024, backupCount=5)  # 10MB na plik, 5 kopii zapasowych
logging.basicConfig(handlers=[handler], level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

# Globalne zmienne
telemetry_lock = Lock()
telemetry_data = {}
running = True
current_car_id = "1"
drive_mode = 2
last_shift_time = 0
last_update_time = 0
last_tire_temp_update = 0
last_speed = None
previous_drive_mode = 2
last_significant_speed_change = 0
kickdown_active = False
last_throttle_drop_time = None 

DRIVE_MODES_DEFAULT = {
    1: {"shift_up": 3390, "shift_down": 1450, "shift_up_cooldown": 0.8, "shift_down_cooldown": 0.8},
    2: {"shift_up": 3890, "shift_down": 2360, "shift_up_cooldown": 1.5, "shift_down_cooldown": 0.8},
    3: {"shift_up": 7550, "shift_down": 4559, "shift_up_cooldown": 0.3, "shift_down_cooldown": 0.3}
}

CAR_SETTINGS_FILE = 'car_settings.json'
PRESETS_FILE = 'presets.json'
car_settings = {"1": copy.copy(DRIVE_MODES_DEFAULT)}
presets = {}

def load_car_settings():
    global car_settings
    try:
        if os.path.exists(CAR_SETTINGS_FILE):
            with open(CAR_SETTINGS_FILE, 'r') as f:
                car_settings = json.load(f)
                logger.info(f"Wczytano car_settings: {car_settings}")
        else:
            car_settings = {"1": copy.copy(DRIVE_MODES_DEFAULT)}
            save_car_settings()
        # Dodatkowe upewnienie się, że wszystkie tryby istnieją
        for car_id in car_settings:
            for mode in DRIVE_MODES_DEFAULT:
                if mode not in car_settings[car_id]:
                    car_settings[car_id][mode] = copy.copy(DRIVE_MODES_DEFAULT[mode])
    except Exception as e:
        logger.error(f"Błąd wczytywania car_settings: {e}")
        car_settings = {"1": copy.copy(DRIVE_MODES_DEFAULT)}
        save_car_settings()

def save_car_settings():
    try:
        with open(CAR_SETTINGS_FILE, 'w') as f:
            json.dump(car_settings, f, indent=4)
        logger.info(f"Zapisano car_settings: {car_settings}")
    except Exception as e:
        logger.error(f"Błąd zapisu car_settings: {e}")

def load_presets():
    global presets
    try:
        if os.path.exists(PRESETS_FILE):
            with open(PRESETS_FILE, 'r') as f:
                content = f.read().strip()
                if not content:
                    logger.info("Plik presets.json pusty – inicjalizuję domyślny słownik.")
                    presets = {}
                    save_presets_to_file()
                else:
                    presets = json.loads(content)
                    for preset_name in presets:
                        for mode in list(presets[preset_name].keys()):
                            presets[preset_name][int(mode)] = presets[preset_name].pop(mode)
                    logger.info(f"Wczytano presety: {presets}")
        else:
            logger.info("Plik presets.json nie istnieje – inicjalizuję domyślny słownik.")
            presets = {}
            save_presets_to_file()
    except Exception as e:
        logger.error(f"Błąd wczytywania presetów: {e}")
        presets = {}
        save_presets_to_file()

def save_presets_to_file():
    try:
        with open(PRESETS_FILE, 'w') as f:
            json.dump(presets, f, indent=4)
        logger.info(f"Zapisano presety: {presets}")
    except Exception as e:
        logger.error(f"Błąd zapisu presetów: {e}")

def free_port(port):
    for conn in psutil.net_connections():
        if conn.laddr.port == port and conn.status == 'LISTEN':
            try:
                process = psutil.Process(conn.pid)
                process_name = process.name().lower()
                if 'python' in process_name and 'forza.py' in ' '.join(process.cmdline()).lower():
                    logger.info(f"Zwalniam port {port} – kończę proces {process_name} (PID: {conn.pid})")
                    process.terminate()
                    time.sleep(1)
                    if process.is_running():
                        process.kill()
                    time.sleep(1)
                    return True
                else:
                    logger.info(f"Port {port} zajęty przez {process_name} (PID: {conn.pid}) – ignoruję.")
                    return False
            except psutil.NoSuchProcess:
                logger.info(f"Proces (PID: {conn.pid}) już nie istnieje.")
                return True
    return True

def find_free_port(start_port=1000, max_port=10000):
    port = start_port
    while port <= max_port:
        if free_port(port):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.bind(('127.0.0.1', port))
                s.close()
                logger.info(f"Znaleziono wolny port: {port}")
                return port
            except socket.error:
                port += 1
                continue
        port += 1
    raise Exception("Nie znaleziono wolnego portu w podanym zakresie!")

def cleanup_socket(socket_obj):
    if socket_obj:
        try:
            socket_obj.shutdown(socket.SHUT_RDWR)  # Zamknij komunikację w obu kierunkach
            socket_obj.close()
            logger.info("Socket zamknięty.")
        except Exception as e:
            logger.error(f"Błąd przy zamykaniu socketu: {e}")

def change_drive_mode(key):
    global drive_mode
    if key.name == '1':
        drive_mode = 1
        logger.info("Tryb jazdy zmieniony na Eco (1)")
    elif key.name == '2':
        drive_mode = 2
        logger.info("Tryb jazdy zmieniony na Normal (2)")
    elif key.name == '3':
        drive_mode = 3
        logger.info("Tryb jazdy zmieniony na Sport (3)")

# Funkcje związane z zarządzaniem biegami
def validate_rpm(current_rpm, idle_rpm, max_rpm):
    """Sprawdza, czy RPM mieści się w dopuszczalnym zakresie."""
    if not (idle_rpm <= current_rpm <= max_rpm):
        logger.warning(f"Nieprawidłowe RPM: {current_rpm} (Idle: {idle_rpm}, Max: {max_rpm})")
        return False
    return True

def execute_gear_shift(key, current_gear, new_gear, current_rpm, action_desc):
    """Wykonuje zmianę biegu za pomocą pyautogui i loguje wynik."""
    try:
        pyautogui.press(key)
        logger.info(f"{action_desc}: {current_gear} -> {new_gear} przy {current_rpm} RPM")
        return True
    except pyautogui.PyAutoGUIException as e:
        logger.error(f"Błąd pyautogui przy {action_desc}: {e}")
        return False

def handle_sport_mode(throttle, brake, current_time):
    """Automatyczne przełączenie na tryb Sport i powrót do poprzedniego trybu."""
    global drive_mode, previous_drive_mode, last_significant_speed_change, last_throttle_drop_time
    if throttle > 90 or brake > 90:
        if drive_mode != 3:
            previous_drive_mode = drive_mode
            drive_mode = 3
            logger.info(f"Automatyczne przełączenie na tryb Sport (3) przy throttle={throttle}, brake={brake}")
        last_significant_speed_change = current_time
        last_throttle_drop_time = None
    elif drive_mode == 3:
        if throttle <= 50 and brake <= 80:
            if last_throttle_drop_time is None:
                last_throttle_drop_time = current_time
            elif current_time - last_throttle_drop_time >= 3:
                drive_mode = previous_drive_mode
                logger.info(f"Responsywny powrót do trybu {drive_mode} po 3s niskiego throttle={throttle}")
                last_throttle_drop_time = None
        elif throttle <= 90 and brake <= 90 and current_time - last_significant_speed_change >= 35:
            drive_mode = previous_drive_mode
            logger.info(f"Powrót do poprzedniego trybu ({drive_mode}) po 35s stabilności")
            last_throttle_drop_time = None
        else:
            last_throttle_drop_time = None

def handle_kickdown(current_rpm, current_gear, throttle, throttle_prev, last_shift_time, current_time, drive_mode_settings):
    """Obsługuje kickdown - nagłą redukcję biegu przy wciśnięciu gazu."""
    global kickdown_active
    shift_down_rpm = drive_mode_settings["shift_down"]
    shift_down_cooldown = drive_mode_settings["shift_down_cooldown"]
    min_kickdown_rpm = drive_mode_settings.get("min_kickdown_rpm", 1500)

    if (throttle > 80 and throttle_prev <= 80 and current_gear > 1 and 
        current_rpm > min_kickdown_rpm and current_rpm < shift_down_rpm and not kickdown_active):
        if current_time - last_shift_time >= shift_down_cooldown:
            new_gear = current_gear - 1
            if execute_gear_shift('q', current_gear, new_gear, current_rpm, "Kickdown: Redukcja biegu"):
                kickdown_active = True
                return new_gear, current_time
            return None, None
    return None, None

def reset_kickdown(throttle):
    """Resetuje stan kickdownu, gdy throttle spada poniżej 80."""
    global kickdown_active
    if throttle <= 80:
        kickdown_active = False

def shift_up(current_rpm, current_gear, throttle, last_shift_time, current_time, drive_mode_settings):
    """Zmiana biegu w górę, gdy RPM przekroczy próg."""
    shift_up_rpm = drive_mode_settings["shift_up"]
    shift_up_cooldown = drive_mode_settings["shift_up_cooldown"]

    if current_rpm >= shift_up_rpm and current_gear < 10 and throttle > 0:
        if current_time - last_shift_time >= shift_up_cooldown:
            new_gear = current_gear + 1
            if execute_gear_shift('e', current_gear, new_gear, current_rpm, "Zmiana biegu w górę"):
                return new_gear, current_time
            return None, None
    return None, None

def shift_down_braking(current_gear, brake, last_shift_time, current_time, drive_mode_settings, current_rpm):
    """Redukcja biegu przy mocnym hamowaniu."""
    shift_down_cooldown = drive_mode_settings["shift_down_cooldown"]

    if brake > 80 and current_gear > 1:
        if current_time - last_shift_time >= shift_down_cooldown:
            new_gear = current_gear - 1
            if execute_gear_shift('q', current_gear, new_gear, current_rpm, "Redukcja przy hamowaniu"):
                return new_gear, current_time
            return None, None
    return None, None

def shift_down_normal(current_rpm, current_gear, throttle, brake, last_shift_time, current_time, drive_mode_settings):
    """Normalna redukcja biegu, gdy RPM spadnie poniżej progu."""
    shift_down_rpm = drive_mode_settings["shift_down"]
    shift_down_cooldown = drive_mode_settings["shift_down_cooldown"]

    if current_rpm < shift_down_rpm and current_gear > 1 and brake <= 80 and throttle <= 80:
        if current_time - last_shift_time >= shift_down_cooldown:
            new_gear = current_gear - 1
            if execute_gear_shift('q', current_gear, new_gear, current_rpm, "Normalna redukcja"):
                return new_gear, current_time
            return None, None
    return None, None

def start_from_neutral(current_gear, throttle, current_rpm, last_shift_time, current_time, drive_mode_settings):
    """Włączenie pierwszego biegu z neutralnego."""
    shift_down_rpm = drive_mode_settings["shift_down"]
    shift_up_cooldown = drive_mode_settings["shift_up_cooldown"]

    if current_gear == 0 and throttle > 0 and current_rpm > shift_down_rpm:
        if current_time - last_shift_time >= shift_up_cooldown:
            new_gear = 1
            if execute_gear_shift('e', current_gear, new_gear, current_rpm, "Start z neutralnego"):
                return new_gear, current_time
            return None, None
    return None, None

def shift_gear(current_rpm, current_gear, drive_mode_settings, last_shift_time, current_time, throttle, brake, idle_rpm, max_rpm, throttle_prev):
    """Funkcja koordynująca automatyczną zmianę biegów."""
    if not validate_rpm(current_rpm, idle_rpm, max_rpm):
        return None, None

    with telemetry_lock:
        handle_sport_mode(throttle, brake, current_time)
        
        new_gear, new_time = handle_kickdown(current_rpm, current_gear, throttle, throttle_prev, last_shift_time, current_time, drive_mode_settings)
        if new_gear is not None:
            return new_gear, new_time

        reset_kickdown(throttle)

        new_gear, new_time = shift_up(current_rpm, current_gear, throttle, last_shift_time, current_time, drive_mode_settings)
        if new_gear is not None:
            return new_gear, new_time

        new_gear, new_time = shift_down_braking(current_gear, brake, last_shift_time, current_time, drive_mode_settings, current_rpm)
        if new_gear is not None:
            return new_gear, new_time

        new_gear, new_time = shift_down_normal(current_rpm, current_gear, throttle, brake, last_shift_time, current_time, drive_mode_settings)
        if new_gear is not None:
            return new_gear, new_time

        new_gear, new_time = start_from_neutral(current_gear, throttle, current_rpm, last_shift_time, current_time, drive_mode_settings)
        if new_gear is not None:
            return new_gear, new_time

    return None, None


def telemetry_thread():
    global telemetry_data, running, current_car_id, drive_mode, last_shift_time, last_update_time, last_tire_temp_update, last_speed, previous_drive_mode, last_significant_speed_change, kickdown_active, last_throttle_drop_time, current_port
    Socket = None
    throttle_prev = 0
    
    try:
        PortNumber = find_free_port()
        logger.info(f"Używam portu {PortNumber} – zmień port w grze na {PortNumber}")
        current_port = PortNumber
        
        HostAddress = "127.0.0.1"
        Socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        Socket.settimeout(0.1)
        Socket.bind((HostAddress, PortNumber))
        logger.info(f"Pomyślnie podłączono do {HostAddress}:{PortNumber}")
        
        atexit.register(cleanup_socket, Socket)
        keyboard.on_press(change_drive_mode)
        
        while running:
            try:
                start_time = time.time()
                UsefullData, addr = Socket.recvfrom(324)
                recv_time = time.time()
                current_time = recv_time
                logger.debug(f"Socket recvfrom latency: {(recv_time - start_time) * 1000:.2f} ms")

                raw_rpm = FHT.get_rpm(UsefullData)
                logger.debug(f"Raw data received - RPM: {raw_rpm}, Time: {current_time}")

                if current_time - last_update_time >= 0.005:
                    with telemetry_lock:
                        car_performance_index = FHT.get_performance_index(UsefullData)
                        if car_performance_index:
                            new_car_id = str(car_performance_index)
                            if new_car_id != current_car_id:
                                current_car_id = new_car_id
                                if current_car_id not in car_settings:
                                    car_settings[current_car_id] = copy.copy(DRIVE_MODES_DEFAULT)
                                    logger.info(f"Zainicjalizowano domyślne ustawienia dla nowego samochodu: {current_car_id}")
                                    save_car_settings()

                        if drive_mode not in car_settings[current_car_id]:
                            car_settings[current_car_id][drive_mode] = copy.copy(DRIVE_MODES_DEFAULT.get(drive_mode, DRIVE_MODES_DEFAULT[2]))
                            logger.info(f"Zainicjalizowano domyślne ustawienia dla trybu {drive_mode} w samochodzie {current_car_id}")
                            save_car_settings()

                        telemetry_data.update({
                            "RaceOn": FHT.get_race_on(UsefullData),
                            "TimeStamp": FHT.get_time_stamp(UsefullData),
                            "MaxRPM": FHT.get_max_rpm(UsefullData),
                            "IdleRPM": FHT.get_idle_rpm(UsefullData),
                            "CurrentRPM": raw_rpm,
                            "Gear": FHT.get_Gear(UsefullData),
                            "CarPerformanceIndex": car_performance_index,
                            "DrivetrainType": FHT.get_drivetrain_type(UsefullData),
                            "NumberOfCylinders": FHT.get_num_of_cyl(UsefullData),
                            "Speed": FHT.get_Speed(UsefullData),
                            "Power": FHT.get_Power(UsefullData),
                            "Torque": FHT.get_Torque(UsefullData),
                            "Throttle": FHT.get_Throttle_status(UsefullData),
                            "Brake": FHT.get_Brake_status(UsefullData),
                            "Clutch": FHT.get_Clutch_status(UsefullData),
                            "HandBrake": FHT.get_HandBrake_status(UsefullData),
                            "Steer": FHT.get_Steering_input(UsefullData),
                            "DistanceTraveled": FHT.get_distance_traveled(UsefullData),
                            "LastShiftTime": last_shift_time,
                            "shift_up": car_settings[current_car_id][drive_mode]["shift_up"],
                            "shift_down": car_settings[current_car_id][drive_mode]["shift_down"],
                            "shift_up_cooldown": car_settings[current_car_id][drive_mode]["shift_up_cooldown"],
                            "shift_down_cooldown": car_settings[current_car_id][drive_mode]["shift_down_cooldown"],
                            "drive_mode": drive_mode,
                            "current_car_id": current_car_id
                        })

                        if current_time - last_tire_temp_update >= 1.0:
                            telemetry_data.update({
                                "TireTempFL": FHT.get_Tire_temp_FL(UsefullData),
                                "TireTempFR": FHT.get_Tire_temp_FR(UsefullData),
                                "TireTempRL": FHT.get_Tire_temp_RL(UsefullData),
                                "TireTempRR": FHT.get_Tire_temp_RR(UsefullData),
                            })
                            logger.info(f"Tire Temps - FL: {telemetry_data['TireTempFL']}, FR: {telemetry_data['TireTempFR']}, RL: {telemetry_data['TireTempRL']}, RR: {telemetry_data['TireTempRR']}")
                            last_tire_temp_update = current_time

                        drive_mode_settings = car_settings[current_car_id][drive_mode]
                        current_rpm = telemetry_data["CurrentRPM"]
                        current_gear = telemetry_data["Gear"]
                        throttle = telemetry_data["Throttle"]
                        brake = telemetry_data["Brake"]
                        current_speed = telemetry_data["Speed"]
                        idle_rpm = telemetry_data["IdleRPM"]
                        max_rpm = telemetry_data["MaxRPM"]

                    if last_speed is not None:
                        speed_change = abs(current_speed - last_speed) / (current_time - last_update_time)
                        if speed_change > 5:
                            with telemetry_lock:
                                last_significant_speed_change = current_time
                            logger.debug(f"Gwałtowna zmiana prędkości: {speed_change:.2f} km/h/s")

                    new_gear, new_shift_time = shift_gear(
                        current_rpm, current_gear, drive_mode_settings, last_shift_time, current_time,
                        throttle, brake, idle_rpm, max_rpm, throttle_prev
                    )
                    if new_gear is not None:
                        with telemetry_lock:
                            telemetry_data["Gear"] = new_gear
                            last_shift_time = new_shift_time

                    # Wysyłanie danych przez WebSocket
                    socketio.emit('telemetry_update', telemetry_data)

                    last_speed = current_speed
                    throttle_prev = throttle
                    last_update_time = current_time

                    logger.debug(f"Telemetry update: RPM={current_rpm}, Gear={current_gear}, Mode={drive_mode}, ShiftUp={drive_mode_settings['shift_up']}")

            except socket.timeout:
                logger.debug("Brak danych telemetrycznych (pauza w grze?) – krótka pauza.")
                time.sleep(0.005)
            except socket.error as e:
                logger.error(f"Błąd odbioru danych: {e}")
                time.sleep(1)
    except Exception as e:
        logger.error(f"Krytyczny błąd w wątku telemetrycznym: {e}", exc_info=True)
        running = False
    finally:
        cleanup_socket(Socket)


@app.route('/', methods=['GET', 'POST'])
def index():
    global drive_mode
    if request.method == 'POST':
        if 'drive_mode' in request.form:
            try:
                drive_mode = int(request.form['drive_mode'])
                logger.info(f"Zmieniono tryb jazdy na: {drive_mode}")
            except ValueError:
                logger.error(f"Nieprawidłowa wartość trybu jazdy: {request.form['drive_mode']}")
                drive_mode = 2
    with telemetry_lock:
        data = telemetry_data.copy()
    return render_template('index.html', telemetry=data, drive_mode=drive_mode, presets=presets, port=current_port)

@app.route('/dashboard')
def dashboard():
    with telemetry_lock:
        data = telemetry_data.copy()
    return render_template('dashboard.html', telemetry=data, drive_mode=drive_mode, current_car_id=current_car_id, car_settings=car_settings)


@app.route('/telemetry')
def get_telemetry():
    start_time = time.time()
    with telemetry_lock:
        data = telemetry_data.copy()
        data['drive_mode'] = drive_mode
        data['last_shift_time'] = float(last_shift_time)
        data['current_car_id'] = current_car_id
        if current_car_id in car_settings and drive_mode in car_settings[current_car_id]:
            data.update({
                'shift_up': car_settings[current_car_id][drive_mode]["shift_up"],
                'shift_down': car_settings[current_car_id][drive_mode]["shift_down"],
                'shift_up_cooldown': float(car_settings[current_car_id][drive_mode]["shift_up_cooldown"]),
                'shift_down_cooldown': float(car_settings[current_car_id][drive_mode]["shift_down_cooldown"])
            })
    response = app.response_class(response=json.dumps(data), mimetype='application/json')
    logger.debug(f"Telemetry endpoint latency: {(time.time() - start_time) * 1000:.2f} ms")
    return response

@app.route('/update_rpm', methods=['POST'])
def update_rpm():
    if request.remote_addr != '127.0.0.1':
        abort(403)
    car_id = request.form.get('car_id', current_car_id)
    mode = int(request.form.get('mode', drive_mode))
    
    if car_id not in car_settings:
        car_settings[car_id] = copy.copy(DRIVE_MODES_DEFAULT)
    
    shift_up = int(request.form.get('shift_up', car_settings[car_id][mode]["shift_up"]))
    shift_down = int(request.form.get('shift_down', car_settings[car_id][mode]["shift_down"]))
    shift_up_cooldown = float(request.form.get('shift_up_cooldown', car_settings[car_id][mode]["shift_up_cooldown"]))
    shift_down_cooldown = float(request.form.get('shift_down_cooldown', car_settings[car_id][mode]["shift_down_cooldown"]))
    
    car_settings[car_id][mode] = {
        "shift_up": shift_up,
        "shift_down": shift_down,
        "shift_up_cooldown": shift_up_cooldown,
        "shift_down_cooldown": shift_down_cooldown
    }
    
    logger.info(f"Zaktualizowano ustawienia dla auta {car_id}, tryb {mode}: shift_up={shift_up}, shift_down={shift_down}")
    save_car_settings()
    return jsonify({"status": "ok", "shift_up": shift_up, "shift_down": shift_down, "shift_up_cooldown": shift_up_cooldown, "shift_down_cooldown": shift_down_cooldown})

@app.route('/save_preset', methods=['POST'])
def save_preset():
    if request.remote_addr != '127.0.0.1':
        abort(403)
    car_id = request.form.get('car_id', current_car_id)
    preset_name = request.form.get('preset_name')
    
    if not preset_name:
        return jsonify({"status": "error", "message": "Nazwa presetu jest wymagana"}), 400
    
    if car_id not in car_settings:
        car_settings[car_id] = copy.copy(DRIVE_MODES_DEFAULT)
    
    presets[preset_name] = copy.copy(car_settings[car_id])
    save_presets_to_file()
    logger.info(f"Zapisano preset '{preset_name}' dla auta {car_id}")
    return jsonify({"status": "ok"})

@app.route('/load_preset', methods=['POST'])
def load_preset():
    if request.remote_addr != '127.0.0.1':
        abort(403)
    car_id = request.form.get('car_id', current_car_id)
    preset_name = request.form.get('preset_name')

    if not preset_name or preset_name not in presets:
        return jsonify({"status": "error", "message": "Preset nie istnieje"}), 400

    car_settings[car_id] = copy.copy(presets[preset_name])
    save_car_settings()
    logger.info(f"Wczytano preset '{preset_name}' dla auta {car_id}")
    return jsonify({"status": "ok"})

@app.route('/status')
def get_status():
    return jsonify({
        "running": running,
        "port": find_free_port(),
        "last_update": time.ctime(last_update_time)
    })

def start_telemetry():
    thread = threading.Thread(target=telemetry_thread)
    thread.daemon = True
    thread.start()

def shutdown():
    global running
    running = False
    save_car_settings()

@socketio.on('connect')
def handle_connect():
    logger.info("Klient podłączony do WebSocket")

if __name__ == '__main__':
    load_car_settings()
    load_presets()
    atexit.register(shutdown)
    try:
        start_telemetry()
        logger.info("Uruchomiono wątek telemetryczny z WebSocket.")
        socketio.run(app, debug=True, host='0.0.0.0', port=5000, use_reloader=False)
    except KeyboardInterrupt:
        shutdown()
        logger.info("Zatrzymano aplikację ręcznie.")
    except Exception as e:
        logger.error(f"Błąd podczas uruchamiania aplikacji: {e}", exc_info=True)
        shutdown()