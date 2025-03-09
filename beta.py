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
from queue import Queue
from datetime import datetime

app = Flask(__name__)
socketio = SocketIO(app)

handler = RotatingFileHandler('log/telemetry_dump.log', maxBytes=10*1024*1024, backupCount=5)  # 10MB na plik, 5 kopii zapasowych
logging.basicConfig(handlers=[handler], level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

# Globalne zmienne (już zdefiniowane wcześniej w kodzie)
telemetry_lock = Lock()
logger = logging.getLogger()
settings_save_queue = Queue()

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
manual_sport_mode = False  # Nowa zmienna śledząca ręczny wybór trybu Sport

current_range = 500  # Starting range in km (adjustable)
fuel_tank_capacity = 50.0  # Fuel tank capacity in liters (adjustable)
short_term_distance = 0.0  # Short-term distance in km
long_term_distance = 0.0  # Long-term distance in km
last_distance_update = 0.0  # Timestamp for distance updates
weather_temperature = 20.0  # Default temperature for Tarnów (will be updated)
weather_condition = "Sunny"  # Default weather condition
fuel_tank_capacity = 50.0  # Fuel tank capacity in liters (adjustable)
car_data = {}  # Dictionary to store per-car data (range, distances, fuel)
last_fuel_update = 0.0  # Timestamp for fuel updates
fuel_consumption_history = []  # List to store fuel consumption history for smoothing

FUEL_UPDATE_INTERVAL = 15.0  # Aktualizacja co 5 sekund dla większych, realistycznych kroków
FUEL_CONSUMPTION_HISTORY_SIZE = 30  # Większa historia dla lepszego wygładzania
MIN_RANGE_CHANGE_DOWN = 5  # Minimalny spadek zasięgu w km
MAX_RANGE_CHANGE_DOWN = 30  # Maksymalny spadek zasięgu w km
MIN_RANGE_CHANGE_UP = 1    # Minimalny wzrost zasięgu w km
MAX_RANGE_CHANGE_UP = 10   # Maksymalny wzrost zasięgu w km


DRIVE_MODES_DEFAULT = {
    1: {"shift_up": 3390, "shift_down": 1450, "shift_up_cooldown": 0.8, "shift_down_cooldown": 0.8},
    2: {"shift_up": 3890, "shift_down": 2360, "shift_up_cooldown": 1.5, "shift_down_cooldown": 0.8},
    3: {"shift_up": 7550, "shift_down": 4559, "shift_up_cooldown": 0.3, "shift_down_cooldown": 0.3},
    4: {"shift_up": 0, "shift_down": 0, "shift_up_cooldown": 0, "shift_down_cooldown": 0}  # Tryb manualny
}

DRIVE_MODE_LABELS = {
    1: "E",  # Eco
    2: "D",  # Normal
    3: "S",  # Sport
    4: "M"   # Manual
}

CAR_SETTINGS_FILE = 'car_settings.json'
PRESETS_FILE = 'presets.json'
car_settings = {"1": copy.copy(DRIVE_MODES_DEFAULT)}
presets = {}

# Update load_car_settings() to include last_traveled_distance
def load_car_settings():
    global car_settings, car_data
    try:
        if os.path.exists(CAR_SETTINGS_FILE):
            with open(CAR_SETTINGS_FILE, 'r') as f:
                car_settings = json.load(f)
                logger.info(f"Wczytano car_settings: {car_settings}")
                for car_id in car_settings:
                    car_data[car_id] = {
                        "long_term_distance": car_settings[car_id].get("long_term_distance", 0.0),
                        "current_range": car_settings[car_id].get("current_range", 500.0),
                        "remaining_fuel": car_settings[car_id].get("remaining_fuel", fuel_tank_capacity),
                        "last_traveled_distance": car_settings[car_id].get("last_traveled_distance", 0.0)
                    }
        else:
            car_settings = {"1": copy.copy(DRIVE_MODES_DEFAULT)}
            car_data["1"] = {
                "long_term_distance": 0.0,
                "current_range": 500.0,
                "remaining_fuel": fuel_tank_capacity,
                "last_traveled_distance": 0.0
            }
            save_car_settings()
        for car_id in car_settings:
            for mode in DRIVE_MODES_DEFAULT:
                if mode not in car_settings[car_id]:
                    car_settings[car_id][mode] = copy.copy(DRIVE_MODES_DEFAULT[mode])
    except Exception as e:
        logger.error(f"Błąd wczytywania car_settings: {e}")
        car_settings = {"1": copy.copy(DRIVE_MODES_DEFAULT)}
        car_data["1"] = {
            "long_term_distance": 0.0,
            "current_range": 500.0,
            "remaining_fuel": fuel_tank_capacity,
            "last_traveled_distance": 0.0
        }
        save_car_settings()

# Update save_car_settings() to include last_traveled_distance
def save_car_settings():
    global car_settings, car_data
    try:
        for car_id in car_data:
            car_settings[car_id]["long_term_distance"] = car_data[car_id]["long_term_distance"]
            car_settings[car_id]["current_range"] = car_data[car_id]["current_range"]
            car_settings[car_id]["remaining_fuel"] = car_data[car_id]["remaining_fuel"]
            car_settings[car_id]["last_traveled_distance"] = car_data[car_id]["last_traveled_distance"]
        with open(CAR_SETTINGS_FILE, 'w') as f:
            json.dump(car_settings, f, indent=4)
        logger.info(f"Zapisano car_settings: {car_settings}")
    except Exception as e:
        logger.error(f"Błąd zapisu car_settings: {e}")

def update_weather():
    global weather_temperature, weather_condition
    current_hour = datetime.now().hour
    current_month = datetime.now().month

    # Simplified temperature model for Tarnów
    if 3 <= current_month <= 5:  # Spring
        base_temp = 10.0
        temp_variation = 5.0 if current_hour > 12 else 0.0
    elif 6 <= current_month <= 8:  # Summer
        base_temp = 20.0
        temp_variation = 7.0 if current_hour > 12 else 2.0
    elif 9 <= current_month <= 11:  # Autumn
        base_temp = 12.0
        temp_variation = 3.0 if current_hour > 12 else -2.0
    else:  # Winter
        base_temp = 0.0
        temp_variation = 2.0 if current_hour > 12 else -5.0

    weather_temperature = base_temp + temp_variation

    # Simplified weather condition model
    if current_hour < 6 or current_hour > 20:
        weather_condition = "Cloudy"
    elif current_month in [6, 7, 8] and current_hour > 10 and current_hour < 18:
        weather_condition = "Sunny"
    else:
        weather_condition = "Partly Cloudy"

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
                # Sprawdzanie właściciela procesu
                if process.username() != os.getlogin():
                    logger.warning(f"Port {port} zajęty przez proces innego użytkownika – pomijam.")
                    return False
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
            except psutil.AccessDenied:
                logger.warning(f"Brak uprawnień do zakończenia procesu na porcie {port}")
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
    global drive_mode, manual_sport_mode
    if key.name == '1':
        drive_mode = 1
        manual_sport_mode = False
        logger.info("Tryb jazdy zmieniony na Eco (E)")
    elif key.name == '2':
        drive_mode = 2
        manual_sport_mode = False
        logger.info("Tryb jazdy zmieniony na Normal (D)")
    elif key.name == '3':
        drive_mode = 3
        manual_sport_mode = True  # Użytkownik ręcznie wybrał tryb Sport
        logger.info("Tryb jazdy zmieniony na Sport (S)")
    elif key.name == '4':
        drive_mode = 4
        manual_sport_mode = False
        logger.info("Tryb jazdy zmieniony na Manual (M)")

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
    """Włączenie pierwszego biegu z neutralnego, ale tylko jeśli nie cofamy."""
    shift_down_rpm = drive_mode_settings["shift_down"]
    shift_up_cooldown = drive_mode_settings["shift_up_cooldown"]

    # Jeśli bieg wsteczny (-1) lub tryb manualny (M), nie wrzucaj biegu 1
    if current_gear == -1 or drive_mode == 4:
        return None, None

    if current_gear == 0 and throttle > 0 and current_rpm > shift_down_rpm:
        if current_time - last_shift_time >= shift_up_cooldown:
            new_gear = 1
            if execute_gear_shift('e', current_gear, new_gear, current_rpm, "Start z neutralnego"):
                return new_gear, current_time
            return None, None
    return None, None

def shift_gear(current_rpm, current_gear, drive_mode_settings, last_shift_time, current_time, throttle, brake, idle_rpm, max_rpm, throttle_prev):
    if not validate_rpm(current_rpm, idle_rpm, max_rpm):
        return None, None

    # Jeśli tryb manualny (M), nie wykonuj automatycznych zmian biegów
    if drive_mode == 4:
        return None, None

    # Obliczenia niezależne od telemetrii przeniesione poza sekcję krytyczną
    handle_sport_mode(throttle, brake, current_time)
    reset_kickdown(throttle)

    with telemetry_lock:
        new_gear, new_time = handle_kickdown(current_rpm, current_gear, throttle, throttle_prev, last_shift_time, current_time, drive_mode_settings)
        if new_gear is not None:
            return new_gear, new_time

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

def handle_sport_mode(throttle, brake, current_time):
    global drive_mode, previous_drive_mode, last_significant_speed_change, last_throttle_drop_time, manual_sport_mode
    new_drive_mode = drive_mode
    new_previous_drive_mode = previous_drive_mode
    new_last_throttle_drop_time = last_throttle_drop_time

    if throttle > 90 or brake > 90:
        if drive_mode != 4 and not manual_sport_mode:
            if drive_mode != 3:
                new_previous_drive_mode = drive_mode
                new_drive_mode = 3
                logger.info(f"Automatyczne przełączenie na tryb Sport (S) przy throttle={throttle}, brake={brake} (kickdown)")
            new_last_throttle_drop_time = None
            new_last_significant_speed_change = current_time  # Ustawienie zmiennej w bloku warunkowym
    elif drive_mode == 3 and not manual_sport_mode:
        if throttle <= 50 and brake <= 80:
            if last_throttle_drop_time is None:
                new_last_throttle_drop_time = current_time
            elif current_time - last_throttle_drop_time >= 3:
                new_drive_mode = new_previous_drive_mode
                logger.info(f"Responsywny powrót do trybu {DRIVE_MODE_LABELS.get(new_drive_mode, 'Unknown')} po 3s niskiego throttle={throttle}")
                new_last_throttle_drop_time = None
        elif throttle <= 90 and brake <= 90 and current_time - last_significant_speed_change >= 35:
            new_drive_mode = new_previous_drive_mode
            logger.info(f"Powrót do poprzedniego trybu ({DRIVE_MODE_LABELS.get(new_drive_mode, 'Unknown')}) po 35s stabilności")
            new_last_throttle_drop_time = None
        else:
            new_last_throttle_drop_time = None

    # Aktualizacja zmiennych globalnych w sekcji krytycznej
    with telemetry_lock:
        drive_mode = new_drive_mode
        previous_drive_mode = new_previous_drive_mode
        last_throttle_drop_time = new_last_throttle_drop_time
        # Przypisanie last_significant_speed_change tylko jeśli zostało zaktualizowane
        if 'new_last_significant_speed_change' in locals():
            last_significant_speed_change = new_last_significant_speed_change

def handle_reverse(event):
    global last_shift_time
    if event.name == 'r':
        with telemetry_lock:
            current_time = time.time()
            current_gear = telemetry_data.get("Gear", 0)  # Pobierz bieżący bieg z telemetry_data
            if current_gear != -1:  # Jeśli nie jesteśmy już na biegu wstecznym
                if execute_gear_shift('q', current_gear, -1, telemetry_data.get("CurrentRPM", 0), "Zmiana na bieg wsteczny"):
                    telemetry_data["Gear"] = -1
                    last_shift_time = current_time
                logger.info("Zmiana na bieg wsteczny (R)")

def save_settings_worker():
    """Wątek zapisujący ustawienia z kolejki, aby uniknąć blokowania wątku telemetrycznego."""
    while running:
        car_id, settings = settings_save_queue.get()
        car_settings[car_id] = settings
        save_car_settings()
        settings_save_queue.task_done()

threading.Thread(target=save_settings_worker, daemon=True).start()

def telemetry_thread():
    global telemetry_data, running, current_car_id, drive_mode, last_shift_time, last_update_time, last_tire_temp_update, last_speed, previous_drive_mode, last_significant_speed_change, kickdown_active, last_throttle_drop_time, current_port, car_data, weather_temperature, weather_condition, last_fuel_update, fuel_consumption_history
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
        keyboard.on_press(lambda e: handle_reverse(e))
        
        short_term_distance = 0.0  # Reset krótkoterminowego dystansu
        last_distance_update = time.time()  # Inicjalizacja czasu ostatniej aktualizacji dystansu
        last_fuel_update = time.time()  # Inicjalizacja czasu ostatniej aktualizacji paliwa
        accumulated_distance = 0.0  # Kumulacja dystansu między aktualizacjami paliwa
        
        while running:
            try:
                start_time = time.time()
                UsefullData, addr = Socket.recvfrom(324)
                recv_time = time.time()
                current_time = recv_time
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(f"Socket recvfrom latency: {(recv_time - start_time) * 1000:.2f} ms")

                raw_rpm = FHT.get_rpm(UsefullData)
                if logger.isEnabledFor(logging.DEBUG):
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
                                    settings_save_queue.put((current_car_id, copy.copy(car_settings[current_car_id])))
                                if current_car_id not in car_data:
                                    car_data[current_car_id] = {
                                        "long_term_distance": 0.0,
                                        "current_range": 500.0,
                                        "remaining_fuel": fuel_tank_capacity,
                                        "last_traveled_distance": 0.0
                                    }
                                    logger.info(f"Zainicjalizowano dane dla nowego samochodu: {current_car_id}")
                                    settings_save_queue.put((current_car_id, copy.copy(car_settings[current_car_id])))

                        if drive_mode not in car_settings[current_car_id]:
                            car_settings[current_car_id][drive_mode] = copy.copy(DRIVE_MODES_DEFAULT.get(drive_mode, DRIVE_MODES_DEFAULT[2]))
                            logger.info(f"Zainicjalizowano domyślne ustawienia dla trybu {drive_mode} w samochodzie {current_car_id}")
                            settings_save_queue.put((current_car_id, copy.copy(car_settings[current_car_id])))

                        # Aktualizacja danych telemetrycznych
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
                            "LastShiftTime": last_shift_time,
                            "shift_up": car_settings[current_car_id][drive_mode]["shift_up"],
                            "shift_down": car_settings[current_car_id][drive_mode]["shift_down"],
                            "shift_up_cooldown": car_settings[current_car_id][drive_mode]["shift_up_cooldown"],
                            "shift_down_cooldown": car_settings[current_car_id][drive_mode]["shift_down_cooldown"],
                            "drive_mode": drive_mode,
                            "drive_mode_label": DRIVE_MODE_LABELS.get(drive_mode, "Unknown"),
                            "current_car_id": current_car_id
                        })

                        # Aktualizacja temperatur opon
                        if current_time - last_tire_temp_update >= 1.0:
                            telemetry_data.update({
                                "TireTempFL": FHT.get_Tire_temp_FL(UsefullData),
                                "TireTempFR": FHT.get_Tire_temp_FR(UsefullData),
                                "TireTempRL": FHT.get_Tire_temp_RL(UsefullData),
                                "TireTempRR": FHT.get_Tire_temp_RR(UsefullData),
                            })
                            logger.info(f"Tire Temps - FL: {telemetry_data['TireTempFL']}, FR: {telemetry_data['TireTempFR']}, RL: {telemetry_data['TireTempRL']}, RR: {telemetry_data['TireTempRR']}")
                            last_tire_temp_update = current_time

                        # Obliczanie dystansu na podstawie prędkości (w km)
                        delta_time = current_time - last_distance_update
                        speed = telemetry_data["Speed"]
                        distance_traveled = (speed / 3600) * delta_time
                        logger.debug(f"Calculated DistanceTraveled: {distance_traveled:.6f} km (Speed={speed} km/h, DeltaTime={delta_time:.3f} s)")

                        # Aktualizacja dystansów
                        short_term_distance += distance_traveled
                        car_data[current_car_id]["long_term_distance"] += distance_traveled
                        car_data[current_car_id]["last_traveled_distance"] += distance_traveled
                        accumulated_distance += distance_traveled

                        # Obliczanie spalania paliwa (L/100 km)
                        current_rpm = telemetry_data["CurrentRPM"]
                        current_speed = telemetry_data["Speed"]
                        max_rpm = telemetry_data["MaxRPM"]
                        idle_rpm = telemetry_data["IdleRPM"]

                        if max_rpm <= 0:
                            logger.warning(f"Invalid MaxRPM value: {max_rpm}. Using fallback value of 8000 RPM.")
                            max_rpm = 8000

                        if current_speed < 5:
                            fuel_consumption = 0.5
                        else:
                            rpm_factor = (current_rpm / max_rpm) * 10
                            speed_factor = (current_speed / 200) * 10
                            fuel_consumption = 5 + (0.5 * rpm_factor + 0.5 * speed_factor)
                            fuel_consumption = max(5, min(20, fuel_consumption))

                        fuel_consumption_history.append(fuel_consumption)
                        if len(fuel_consumption_history) > FUEL_CONSUMPTION_HISTORY_SIZE:
                            fuel_consumption_history.pop(0)
                        smoothed_fuel_consumption = sum(fuel_consumption_history) / len(fuel_consumption_history)

                        # Aktualizacja paliwa i zasięgu co FUEL_UPDATE_INTERVAL
                        if current_time - last_fuel_update >= FUEL_UPDATE_INTERVAL and telemetry_data["RaceOn"] and telemetry_data["MaxRPM"] > 0:
                            # Obliczanie zużytego paliwa
                            fuel_used = (smoothed_fuel_consumption / 100) * accumulated_distance
                            car_data[current_car_id]["remaining_fuel"] -= fuel_used

                            if car_data[current_car_id]["remaining_fuel"] < 0:
                                car_data[current_car_id]["remaining_fuel"] = 0

                            # Obliczanie nowego zasięgu
                            if smoothed_fuel_consumption > 0:
                                new_range = (car_data[current_car_id]["remaining_fuel"] / smoothed_fuel_consumption) * 100
                                current_range = car_data[current_car_id]["current_range"]

                                # Realistyczne zmiany zasięgu zależne od dystansu
                                range_diff = new_range - current_range
                                if range_diff < 0:  # Spadek zasięgu
                                    # Skala zmiany zależna od przebytego dystansu
                                    change = max(MIN_RANGE_CHANGE_DOWN, min(MAX_RANGE_CHANGE_DOWN, abs(range_diff) * accumulated_distance / 10))
                                    car_data[current_car_id]["current_range"] -= round(change)
                                elif range_diff > 0 and accumulated_distance > 1.0:  # Wzrost tylko przy istotnym dystansie
                                    change = max(MIN_RANGE_CHANGE_UP, min(MAX_RANGE_CHANGE_UP, abs(range_diff) * 0.5))
                                    car_data[current_car_id]["current_range"] += round(change)

                                # Ograniczenie zasięgu do maksymalnej wartości (np. 500 km)
                                car_data[current_car_id]["current_range"] = min(500, max(0, car_data[current_car_id]["current_range"]))
                            else:
                                car_data[current_car_id]["current_range"] = 0

                            # Logowanie
                            logger.debug(f"Fuel Update: Speed={current_speed:.1f} km/h, Accumulated Distance={accumulated_distance:.3f} km, Fuel Used={fuel_used:.3f} L, Remaining Fuel={car_data[current_car_id]['remaining_fuel']:.1f} L, Range={car_data[current_car_id]['current_range']} km, Consumption={smoothed_fuel_consumption:.1f} L/100 km")

                            last_fuel_update = current_time
                            accumulated_distance = 0.0
                            settings_save_queue.put((current_car_id, copy.copy(car_settings[current_car_id])))

                        # Aktualizacja danych telemetrycznych
                        telemetry_data.update({
                            "FuelConsumption": round(smoothed_fuel_consumption, 1),
                            "CurrentRange": car_data[current_car_id]["current_range"],
                            "ShortTermDistance": round(short_term_distance, 1),
                            "LongTermDistance": round(car_data[current_car_id]["long_term_distance"], 1),
                            "LocalTime": datetime.now().strftime("%H:%M"),
                            "WeatherTemperature": round(weather_temperature, 1),
                            "WeatherCondition": weather_condition,
                            "RemainingFuel": round(car_data[current_car_id]["remaining_fuel"], 1)
                        })

                        # Aktualizacja pogody co 5 minut
                        if current_time - last_distance_update >= 300:
                            update_weather()

                        drive_mode_settings = car_settings[current_car_id][drive_mode]
                        current_gear = telemetry_data["Gear"]
                        throttle = telemetry_data["Throttle"]
                        brake = telemetry_data["Brake"]

                    if last_speed is not None:
                        speed_change = abs(telemetry_data["Speed"] - last_speed) / (current_time - last_update_time)
                        if speed_change > 5:
                            with telemetry_lock:
                                last_significant_speed_change = current_time
                            if logger.isEnabledFor(logging.DEBUG):
                                logger.debug(f"Gwałtowna zmiana prędkości: {speed_change:.2f} km/h/s")

                    new_gear, new_shift_time = shift_gear(
                        current_rpm, current_gear, drive_mode_settings, last_shift_time, current_time,
                        throttle, brake, idle_rpm, max_rpm, throttle_prev
                    )
                    if new_gear is not None:
                        with telemetry_lock:
                            telemetry_data["Gear"] = new_gear
                            last_shift_time = new_shift_time

                    if socketio.server.eio.sockets:
                        socketio.emit('telemetry_update', telemetry_data)

                    last_speed = telemetry_data["Speed"]
                    throttle_prev = throttle
                    last_update_time = current_time
                    last_distance_update = current_time

                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug(f"Telemetry update: RPM={current_rpm}, Gear={current_gear}, Mode={drive_mode}, ShiftUp={drive_mode_settings['shift_up']}")

            except socket.timeout:
                if logger.isEnabledFor(logging.DEBUG):
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
    global drive_mode, manual_sport_mode
    if request.method == 'POST':
        if 'drive_mode' in request.form:
            try:
                new_mode = int(request.form['drive_mode'])
                drive_mode = new_mode
                manual_sport_mode = (new_mode == 3)  # True tylko dla ręcznie wybranego trybu Sport
                logger.info(f"Zmieniono tryb jazdy na: {DRIVE_MODE_LABELS.get(drive_mode, 'Unknown')}")
            except ValueError:
                logger.error(f"Nieprawidłowa wartość trybu jazdy: {request.form['drive_mode']}")
                drive_mode = 2
                manual_sport_mode = False
    with telemetry_lock:
        data = telemetry_data.copy()
    return render_template('index.html', telemetry=data, drive_mode=drive_mode, presets=presets, port=current_port)

@app.route('/dashboard')
def dashboard():
    with telemetry_lock:
        data = telemetry_data.copy()
    return render_template('dashboard.html', telemetry=data, drive_mode=drive_mode, current_car_id=current_car_id, car_settings=car_settings)

@app.route('/audi')
def audi():
    with telemetry_lock:
        data = telemetry_data.copy()
    return render_template('audi.html', telemetry=data, drive_mode=drive_mode, current_car_id=current_car_id, car_settings=car_settings)


@app.route('/telemetry')
def get_telemetry():
    start_time = time.time()
    with telemetry_lock:
        data = telemetry_data.copy()
        data['drive_mode'] = drive_mode
        data['drive_mode_label'] = DRIVE_MODE_LABELS.get(drive_mode, "Unknown")  # Dodanie oznaczenia trybu
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

    try:
        car_id = request.form.get('car_id', current_car_id)
        mode = int(request.form.get('mode', drive_mode))
        shift_up = int(request.form['shift_up'])
        shift_down = int(request.form['shift_down'])
        shift_up_cooldown = float(request.form['shift_up_cooldown'])
        shift_down_cooldown = float(request.form['shift_down_cooldown'])

        # Walidacja danych wejściowych
        if not (0 < shift_up < 10000 and 0 < shift_down < 10000):
            return jsonify({"status": "error", "message": "Nieprawidłowe wartości RPM"}), 400
        if not (0 <= shift_up_cooldown <= 10 and 0 <= shift_down_cooldown <= 10):
            return jsonify({"status": "error", "message": "Nieprawidłowe wartości cooldown"}), 400
    except (KeyError, ValueError):
        return jsonify({"status": "error", "message": "Nieprawidłowe dane wejściowe"}), 400

    if car_id not in car_settings:
        car_settings[car_id] = copy.copy(DRIVE_MODES_DEFAULT)
    
    car_settings[car_id][mode] = {
        "shift_up": shift_up,
        "shift_down": shift_down,
        "shift_up_cooldown": shift_up_cooldown,
        "shift_down_cooldown": shift_down_cooldown
    }
    
    logger.info(f"Zaktualizowano ustawienia dla auta {car_id}, tryb {mode}: shift_up={shift_up}, shift_down={shift_down}")
    # Dodanie do kolejki zamiast bezpośredniego zapisu
    settings_save_queue.put((car_id, copy.copy(car_settings[car_id])))
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