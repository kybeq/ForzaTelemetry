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

# Inicjalizacja aplikacji Flask i SocketIO
app = Flask(__name__)
socketio = SocketIO(app)

# Konfiguracja logowania
handler = RotatingFileHandler('log/telemetry_dump.log', maxBytes=10*1024*1024, backupCount=5)
logging.basicConfig(handlers=[handler], level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

# Stałe
DRIVE_MODES_DEFAULT = {
    1: {"shift_up": 3390, "shift_down": 1450, "shift_up_cooldown": 0.8, "shift_down_cooldown": 0.8},
    2: {"shift_up": 3890, "shift_down": 2360, "shift_up_cooldown": 1.5, "shift_down_cooldown": 0.8},
    3: {"shift_up": 7550, "shift_down": 4559, "shift_up_cooldown": 0.3, "shift_down_cooldown": 0.3},
    4: {"shift_up": 0, "shift_down": 0, "shift_up_cooldown": 0, "shift_down_cooldown": 0}
}

DRIVE_MODE_LABELS = {
    1: "E", 2: "D", 3: "S", 4: "M"
}

CAR_SETTINGS_FILE = 'car_settings.json'
PRESETS_FILE = 'presets.json'

# Stałe związane z paliwem
FUEL_UPDATE_INTERVAL = 15.0  # Aktualizacja co 15 sekund
FUEL_CONSUMPTION_HISTORY_SIZE = 30  # Historia dla wygładzania
MIN_RANGE_CHANGE_DOWN = 5  # Minimalny spadek zasięgu w km
MAX_RANGE_CHANGE_DOWN = 30  # Maksymalny spadek zasięgu w km
MIN_RANGE_CHANGE_UP = 1    # Minimalny wzrost zasięgu w km
MAX_RANGE_CHANGE_UP = 10   # Maksymalny wzrost zasięgu w km

# Globalne zmienne
running = True
current_port = 0

class AppState:
    def __init__(self):
        self.lock = Lock()
        self.telemetry_data = {}
        self.drive_mode = 2
        self.current_car_id = "1"
        self.last_shift_time = 0
        self.last_update_time = 0
        self.last_tire_temp_update = 0
        self.last_speed = None
        self.previous_drive_mode = 2
        self.last_significant_speed_change = 0
        self.kickdown_active = False
        self.last_throttle_drop_time = None
        self.manual_sport_mode = False
        self.current_range = 500
        self.fuel_tank_capacity = 50.0
        self.short_term_distance = 0.0
        self.long_term_distance = 0.0
        self.last_distance_update = 0.0
        self.weather_temperature = 20.0
        self.weather_condition = "Sunny"
        self.last_fuel_update = 0.0
        self.fuel_consumption_history = []
        self.car_settings = {"1": copy.deepcopy(DRIVE_MODES_DEFAULT)}
        self.presets = {}
        self.car_data = {"1": {
            "long_term_distance": 0.0,
            "current_range": 500.0,
            "remaining_fuel": self.fuel_tank_capacity,
            "last_traveled_distance": 0.0
        }}
        self.settings_save_queue = Queue()

    def update_telemetry(self, data):
        with self.lock:
            self.telemetry_data.update(data)

    def get_telemetry(self):
        with self.lock:
            return self.telemetry_data.copy()

class GearboxManager:
    def __init__(self, app_state):
        self.app_state = app_state

    def validate_rpm(self, current_rpm, idle_rpm, max_rpm):
        if not (idle_rpm <= current_rpm <= max_rpm):
            logger.warning(f"Nieprawidłowe RPM: {current_rpm} (Idle: {idle_rpm}, Max: {max_rpm})")
            return False
        return True

    def handle_sport_mode(self, throttle, brake, current_time):
        if throttle > 90 or brake > 90:
            if self.app_state.drive_mode != 4 and not self.app_state.manual_sport_mode:
                if self.app_state.drive_mode != 3:
                    self.app_state.previous_drive_mode = self.app_state.drive_mode
                    self.app_state.drive_mode = 3
                    logger.info(f"Automatyczne przełączenie na tryb Sport (S) przy throttle={throttle}, brake={brake}")
                self.app_state.last_throttle_drop_time = None
                self.app_state.last_significant_speed_change = current_time
        elif self.app_state.drive_mode == 3 and not self.app_state.manual_sport_mode:
            if throttle <= 50 and brake <= 80:
                if self.app_state.last_throttle_drop_time is None:
                    self.app_state.last_throttle_drop_time = current_time
                elif current_time - self.app_state.last_throttle_drop_time >= 3:
                    self.app_state.drive_mode = self.app_state.previous_drive_mode
                    logger.info(f"Powrót do trybu {DRIVE_MODE_LABELS.get(self.app_state.drive_mode, 'Unknown')} po 3s")
                    self.app_state.last_throttle_drop_time = None
            elif throttle <= 90 and brake <= 90 and current_time - self.app_state.last_significant_speed_change >= 35:
                self.app_state.drive_mode = self.app_state.previous_drive_mode
                logger.info(f"Powrót do trybu {DRIVE_MODE_LABELS.get(self.app_state.drive_mode, 'Unknown')} po 35s")
                self.app_state.last_throttle_drop_time = None
            else:
                self.app_state.last_throttle_drop_time = None

    def reset_kickdown(self, throttle):
        if throttle <= 80:
            self.app_state.kickdown_active = False

    def shift_up(self, current_rpm, current_gear, throttle, last_shift_time, current_time, drive_mode_settings):
        shift_up_rpm = drive_mode_settings["shift_up"]
        shift_up_cooldown = drive_mode_settings["shift_up_cooldown"]
        if current_rpm >= shift_up_rpm and current_gear < 10 and throttle > 0:
            if current_time - last_shift_time >= shift_up_cooldown:
                new_gear = current_gear + 1
                if execute_gear_shift('e', current_gear, new_gear, current_rpm, "Zmiana biegu w górę"):
                    return new_gear, current_time
        return None, None

    def shift_down(self, current_rpm, current_gear, throttle, brake, last_shift_time, current_time, drive_mode_settings):
        shift_down_rpm = drive_mode_settings["shift_down"]
        shift_down_cooldown = drive_mode_settings["shift_down_cooldown"]
        if current_rpm < shift_down_rpm and current_gear > 1 and brake <= 80 and throttle <= 80:
            if current_time - last_shift_time >= shift_down_cooldown:
                new_gear = current_gear - 1
                if execute_gear_shift('q', current_gear, new_gear, current_rpm, "Normalna redukcja"):
                    return new_gear, current_time
        return None, None

    def shift_down_braking(self, current_gear, brake, last_shift_time, current_time, drive_mode_settings, current_rpm):
        shift_down_cooldown = drive_mode_settings["shift_down_cooldown"]
        if brake > 80 and current_gear > 1:
            if current_time - last_shift_time >= shift_down_cooldown:
                new_gear = current_gear - 1
                if execute_gear_shift('q', current_gear, new_gear, current_rpm, "Redukcja przy hamowaniu"):
                    return new_gear, current_time
        return None, None

    def start_from_neutral(self, current_gear, throttle, current_rpm, last_shift_time, current_time, drive_mode_settings):
        shift_down_rpm = drive_mode_settings["shift_down"]
        shift_up_cooldown = drive_mode_settings["shift_up_cooldown"]
        if current_gear == 0 and throttle > 0 and current_rpm > shift_down_rpm:
            if current_time - last_shift_time >= shift_up_cooldown:
                new_gear = 1
                if execute_gear_shift('e', current_gear, new_gear, current_rpm, "Start z neutralnego"):
                    return new_gear, current_time
        return None, None

    def handle_kickdown(self, current_rpm, current_gear, throttle, throttle_prev, last_shift_time, current_time, drive_mode_settings):
        shift_down_rpm = drive_mode_settings["shift_down"]
        shift_down_cooldown = drive_mode_settings["shift_down_cooldown"]
        min_kickdown_rpm = drive_mode_settings.get("min_kickdown_rpm", 1500)
        if (throttle > 80 and throttle_prev <= 80 and current_gear > 1 and 
            current_rpm > min_kickdown_rpm and current_rpm < shift_down_rpm and not self.app_state.kickdown_active):
            if current_time - last_shift_time >= shift_down_cooldown:
                new_gear = current_gear - 1
                if execute_gear_shift('q', current_gear, new_gear, current_rpm, "Kickdown: Redukcja biegu"):
                    self.app_state.kickdown_active = True
                    return new_gear, current_time
        return None, None

def load_car_settings(app_state):
    try:
        if os.path.exists(CAR_SETTINGS_FILE):
            with open(CAR_SETTINGS_FILE, 'r') as f:
                app_state.car_settings = json.load(f)
                for car_id in app_state.car_settings:
                    app_state.car_data[car_id] = {
                        "long_term_distance": app_state.car_settings[car_id].get("long_term_distance", 0.0),
                        "current_range": app_state.car_settings[car_id].get("current_range", 500.0),
                        "remaining_fuel": app_state.car_settings[car_id].get("remaining_fuel", app_state.fuel_tank_capacity),
                        "last_traveled_distance": app_state.car_settings[car_id].get("last_traveled_distance", 0.0)
                    }
        else:
            app_state.car_settings = {"1": copy.deepcopy(DRIVE_MODES_DEFAULT)}
            app_state.car_data["1"] = {
                "long_term_distance": 0.0,
                "current_range": 500.0,
                "remaining_fuel": app_state.fuel_tank_capacity,
                "last_traveled_distance": 0.0
            }
            save_car_settings(app_state)
    except Exception as e:
        logger.error(f"Błąd wczytywania car_settings: {e}")
        app_state.car_settings = {"1": copy.deepcopy(DRIVE_MODES_DEFAULT)}
        app_state.car_data["1"] = {
            "long_term_distance": 0.0,
            "current_range": 500.0,
            "remaining_fuel": app_state.fuel_tank_capacity,
            "last_traveled_distance": 0.0
        }
        save_car_settings(app_state)

def save_car_settings(app_state):
    try:
        for car_id in app_state.car_data:
            app_state.car_settings[car_id]["long_term_distance"] = app_state.car_data[car_id]["long_term_distance"]
            app_state.car_settings[car_id]["current_range"] = app_state.car_data[car_id]["current_range"]
            app_state.car_settings[car_id]["remaining_fuel"] = app_state.car_data[car_id]["remaining_fuel"]
            app_state.car_settings[car_id]["last_traveled_distance"] = app_state.car_data[car_id]["last_traveled_distance"]
        with open(CAR_SETTINGS_FILE, 'w') as f:
            json.dump(app_state.car_settings, f, indent=4)
        logger.info(f"Zapisano car_settings")
    except Exception as e:
        logger.error(f"Błąd zapisu car_settings: {e}")

def save_settings_worker(app_state):
    while running:
        car_id, settings = app_state.settings_save_queue.get()
        app_state.car_settings[car_id] = settings
        save_car_settings(app_state)
        app_state.settings_save_queue.task_done()

def load_presets(app_state):
    try:
        if os.path.exists(PRESETS_FILE):
            with open(PRESETS_FILE, 'r') as f:
                app_state.presets = json.load(f)
                for preset_name in app_state.presets:
                    for mode in list(app_state.presets[preset_name].keys()):
                        app_state.presets[preset_name][int(mode)] = app_state.presets[preset_name].pop(mode)
        else:
            app_state.presets = {}
            save_presets(app_state)
    except Exception as e:
        logger.error(f"Błąd wczytywania presetów: {e}")
        app_state.presets = {}
        save_presets(app_state)

def save_presets(app_state):
    try:
        with open(PRESETS_FILE, 'w') as f:
            json.dump(app_state.presets, f, indent=4)
        logger.info(f"Zapisano presety")
    except Exception as e:
        logger.error(f"Błąd zapisu presetów: {e}")

def free_port(port):
    for conn in psutil.net_connections():
        if conn.laddr.port == port and conn.status == 'LISTEN':
            try:
                process = psutil.Process(conn.pid)
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
                return port
            except socket.error:
                port += 1
                continue
        port += 1
    raise Exception("Nie znaleziono wolnego portu w podanym zakresie!")

def cleanup_socket(sock):
    if sock:
        try:
            sock.shutdown(socket.SHUT_RDWR)
            sock.close()
            logger.info("Socket zamknięty.")
        except Exception as e:
            logger.error(f"Błąd przy zamykaniu socketu: {e}")

def execute_gear_shift(key, old_gear, new_gear, rpm, reason):
    try:
        pyautogui.press(key)
        logger.info(f"{reason}: {old_gear} -> {new_gear} przy {rpm} RPM")
        return True
    except pyautogui.PyAutoGUIException as e:
        logger.error(f"Błąd pyautogui przy {reason}: {e}")
        return False

def change_drive_mode(key, app_state):
    if key.name == '1':
        app_state.drive_mode = 1
        app_state.manual_sport_mode = False
        logger.info("Tryb jazdy: Eco (E)")
    elif key.name == '2':
        app_state.drive_mode = 2
        app_state.manual_sport_mode = False
        logger.info("Tryb jazdy: Normal (D)")
    elif key.name == '3':
        app_state.drive_mode = 3
        app_state.manual_sport_mode = True
        logger.info("Tryb jazdy: Sport (S)")
    elif key.name == '4':
        app_state.drive_mode = 4
        app_state.manual_sport_mode = False
        logger.info("Tryb jazdy: Manual (M)")

def handle_reverse(event, app_state):
    if event.name == 'r':
        with app_state.lock:
            current_time = time.time()
            current_gear = app_state.telemetry_data.get("Gear", 0)
            if current_gear != -1:
                if execute_gear_shift('q', current_gear, -1, app_state.telemetry_data.get("CurrentRPM", 0), "Zmiana na bieg wsteczny"):
                    app_state.telemetry_data["Gear"] = -1
                    app_state.last_shift_time = current_time
                logger.info("Zmiana na bieg wsteczny (R)")

def update_weather(app_state):
    current_hour = datetime.now().hour
    current_month = datetime.now().month

    if 3 <= current_month <= 5:  # Wiosna
        base_temp = 10.0
        temp_variation = 5.0 if current_hour > 12 else 0.0
    elif 6 <= current_month <= 8:  # Lato
        base_temp = 20.0
        temp_variation = 7.0 if current_hour > 12 else 2.0
    elif 9 <= current_month <= 11:  # Jesień
        base_temp = 12.0
        temp_variation = 3.0 if current_hour > 12 else -2.0
    else:  # Zima
        base_temp = 0.0
        temp_variation = 2.0 if current_hour > 12 else -5.0

    app_state.weather_temperature = base_temp + temp_variation

    if current_hour < 6 or current_hour > 20:
        app_state.weather_condition = "Cloudy"
    elif current_month in [6, 7, 8] and current_hour > 10 and current_hour < 18:
        app_state.weather_condition = "Sunny"
    else:
        app_state.weather_condition = "Partly Cloudy"

def telemetry_thread(app_state, gearbox_manager):
    global running, current_port
    Socket = None
    throttle_prev = 0
    try:
        PortNumber = find_free_port()
        current_port = PortNumber
        logger.info(f"Używam portu {PortNumber} – zmień port w grze na {PortNumber}")
        HostAddress = "127.0.0.1"
        Socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        Socket.settimeout(0.1)
        Socket.bind((HostAddress, PortNumber))
        logger.info(f"Pomyślnie podłączono do {HostAddress}:{PortNumber}")
        
        atexit.register(cleanup_socket, Socket)
        keyboard.on_press(lambda key: change_drive_mode(key, app_state))
        keyboard.on_press(lambda e: handle_reverse(e, app_state))
        
        accumulated_distance = 0.0
        last_weather_update = 0.0
        
        while running:
            try:
                start_time = time.time()  # Poprawiono literówkę "tart_time"
                UsefullData, addr = Socket.recvfrom(324)
                recv_time = time.time()
                current_time = recv_time
                
                raw_rpm = FHT.get_rpm(UsefullData)
                
                if current_time - app_state.last_update_time >= 0.005:
                    with app_state.lock:
                        car_performance_index = FHT.get_performance_index(UsefullData)
                        if car_performance_index:
                            new_car_id = str(car_performance_index)
                            if new_car_id != app_state.current_car_id:
                                app_state.current_car_id = new_car_id
                                if new_car_id not in app_state.car_settings:
                                    app_state.car_settings[new_car_id] = copy.deepcopy(DRIVE_MODES_DEFAULT)
                                    app_state.car_data[new_car_id] = {
                                        "long_term_distance": 0.0,
                                        "current_range": 500.0,
                                        "remaining_fuel": app_state.fuel_tank_capacity,
                                        "last_traveled_distance": 0.0
                                    }
                                    app_state.settings_save_queue.put((new_car_id, copy.deepcopy(app_state.car_settings[new_car_id])))

                        if app_state.drive_mode not in app_state.car_settings[app_state.current_car_id]:
                            app_state.car_settings[app_state.current_car_id][app_state.drive_mode] = copy.deepcopy(DRIVE_MODES_DEFAULT.get(app_state.drive_mode, DRIVE_MODES_DEFAULT[2]))
                            app_state.settings_save_queue.put((app_state.current_car_id, copy.deepcopy(app_state.car_settings[app_state.current_car_id])))

                        telemetry_data = {
                            "RaceOn": FHT.get_race_on(UsefullData),
                            "TimeStamp": FHT.get_time_stamp(UsefullData),
                            "MaxRPM": FHT.get_max_rpm(UsefullData),
                            "IdleRPM": FHT.get_idle_rpm(UsefullData),
                            "CurrentRPM": raw_rpm,
                            "Gear": FHT.get_Gear(UsefullData),
                            "CarPerformanceIndex": car_performance_index,
                            "Speed": FHT.get_Speed(UsefullData),
                            "Throttle": FHT.get_Throttle_status(UsefullData),
                            "Brake": FHT.get_Brake_status(UsefullData),
                            "drive_mode": app_state.drive_mode,
                            "drive_mode_label": DRIVE_MODE_LABELS.get(app_state.drive_mode, "Unknown"),
                            "current_car_id": app_state.current_car_id
                        }
                        app_state.update_telemetry(telemetry_data)

                        current_rpm = telemetry_data["CurrentRPM"]
                        current_gear = telemetry_data["Gear"]
                        throttle = telemetry_data["Throttle"]
                        brake = telemetry_data["Brake"]
                        idle_rpm = telemetry_data["IdleRPM"]
                        max_rpm = telemetry_data["MaxRPM"]
                        drive_mode_settings = app_state.car_settings[app_state.current_car_id][app_state.drive_mode]

                        if app_state.drive_mode != 4 and gearbox_manager.validate_rpm(current_rpm, idle_rpm, max_rpm):
                            gearbox_manager.handle_sport_mode(throttle, brake, current_time)
                            gearbox_manager.reset_kickdown(throttle)
                            new_gear, new_time = gearbox_manager.handle_kickdown(current_rpm, current_gear, throttle, throttle_prev, app_state.last_shift_time, current_time, drive_mode_settings)
                            if new_gear is not None:
                                app_state.last_shift_time = new_time
                                telemetry_data["Gear"] = new_gear
                            else:
                                new_gear, new_time = gearbox_manager.shift_up(current_rpm, current_gear, throttle, app_state.last_shift_time, current_time, drive_mode_settings)
                                if new_gear is not None:
                                    app_state.last_shift_time = new_time
                                    telemetry_data["Gear"] = new_gear
                                else:
                                    new_gear, new_time = gearbox_manager.shift_down_braking(current_gear, brake, app_state.last_shift_time, current_time, drive_mode_settings, current_rpm)
                                    if new_gear is not None:
                                        app_state.last_shift_time = new_time
                                        telemetry_data["Gear"] = new_gear
                                    else:
                                        new_gear, new_time = gearbox_manager.shift_down(current_rpm, current_gear, throttle, brake, app_state.last_shift_time, current_time, drive_mode_settings)
                                        if new_gear is not None:
                                            app_state.last_shift_time = new_time
                                            telemetry_data["Gear"] = new_gear
                                        else:
                                            new_gear, new_time = gearbox_manager.start_from_neutral(current_gear, throttle, current_rpm, app_state.last_shift_time, current_time, drive_mode_settings)
                                            if new_gear is not None:
                                                app_state.last_shift_time = new_time
                                                telemetry_data["Gear"] = new_gear

                        delta_time = current_time - app_state.last_distance_update
                        speed = telemetry_data["Speed"]
                        distance_traveled = (speed / 3600) * delta_time
                        app_state.short_term_distance += distance_traveled
                        app_state.car_data[app_state.current_car_id]["long_term_distance"] += distance_traveled
                        app_state.car_data[app_state.current_car_id]["last_traveled_distance"] += distance_traveled
                        accumulated_distance += distance_traveled

                        # Obliczanie spalania paliwa (L/100 km)
                        if max_rpm <= 0:
                            max_rpm = 8000  # Fallback value
                        if speed < 5:
                            fuel_consumption = 0.5
                        else:
                            rpm_factor = (current_rpm / max_rpm) * 10
                            speed_factor = (speed / 200) * 10
                            fuel_consumption = 5 + (0.5 * rpm_factor + 0.5 * speed_factor)
                            fuel_consumption = max(5, min(20, fuel_consumption))

                        app_state.fuel_consumption_history.append(fuel_consumption)
                        if len(app_state.fuel_consumption_history) > FUEL_CONSUMPTION_HISTORY_SIZE:
                            app_state.fuel_consumption_history.pop(0)
                        smoothed_fuel_consumption = sum(app_state.fuel_consumption_history) / len(app_state.fuel_consumption_history)

                        # Aktualizacja paliwa i zasięgu co FUEL_UPDATE_INTERVAL
                        if current_time - app_state.last_fuel_update >= FUEL_UPDATE_INTERVAL and telemetry_data["RaceOn"] and telemetry_data["MaxRPM"] > 0:
                            fuel_used = (smoothed_fuel_consumption / 100) * accumulated_distance
                            app_state.car_data[app_state.current_car_id]["remaining_fuel"] -= fuel_used
                            if app_state.car_data[app_state.current_car_id]["remaining_fuel"] < 0:
                                app_state.car_data[app_state.current_car_id]["remaining_fuel"] = 0

                            if smoothed_fuel_consumption > 0:
                                new_range = (app_state.car_data[app_state.current_car_id]["remaining_fuel"] / smoothed_fuel_consumption) * 100
                                current_range = app_state.car_data[app_state.current_car_id]["current_range"]
                                range_diff = new_range - current_range
                                if range_diff < 0:
                                    change = max(MIN_RANGE_CHANGE_DOWN, min(MAX_RANGE_CHANGE_DOWN, abs(range_diff) * accumulated_distance / 10))
                                    app_state.car_data[app_state.current_car_id]["current_range"] -= round(change)
                                elif range_diff > 0 and accumulated_distance > 1.0:
                                    change = max(MIN_RANGE_CHANGE_UP, min(MAX_RANGE_CHANGE_UP, abs(range_diff) * 0.5))
                                    app_state.car_data[app_state.current_car_id]["current_range"] += round(change)
                                app_state.car_data[app_state.current_car_id]["current_range"] = min(500, max(0, app_state.car_data[app_state.current_car_id]["current_range"]))
                            else:
                                app_state.car_data[app_state.current_car_id]["current_range"] = 0

                            app_state.last_fuel_update = current_time
                            accumulated_distance = 0.0
                            app_state.settings_save_queue.put((app_state.current_car_id, copy.deepcopy(app_state.car_settings[app_state.current_car_id])))

                        # Aktualizacja pogody co 5 minut
                        if current_time - last_weather_update >= 300:
                            update_weather(app_state)
                            last_weather_update = current_time

                        # Dodanie danych paliwa i pogody do telemetrii
                        telemetry_data.update({
                            "FuelConsumption": round(smoothed_fuel_consumption, 1),
                            "CurrentRange": app_state.car_data[app_state.current_car_id]["current_range"],
                            "ShortTermDistance": round(app_state.short_term_distance, 1),
                            "LongTermDistance": round(app_state.car_data[app_state.current_car_id]["long_term_distance"], 1),
                            "LocalTime": datetime.now().strftime("%H:%M"),
                            "WeatherTemperature": round(app_state.weather_temperature, 1),
                            "WeatherCondition": app_state.weather_condition,
                            "RemainingFuel": round(app_state.car_data[app_state.current_car_id]["remaining_fuel"], 1)
                        })

                    if socketio.server.eio.sockets:
                        socketio.emit('telemetry_update', app_state.get_telemetry())

                    throttle_prev = throttle
                    app_state.last_update_time = current_time
                    app_state.last_distance_update = current_time

            except socket.timeout:
                time.sleep(0.005)  # Krótkie oczekiwanie przy braku danych
            except socket.error as e:
                logger.error(f"Błąd odbioru danych: {e}")
                time.sleep(1)  # Dłuższe oczekiwanie przy błędzie
                continue  # Kontynuuj pętlę zamiast kończyć
    except Exception as e:
        logger.error(f"Krytyczny błąd w wątku telemetrycznym: {e}", exc_info=True)
    finally:
        cleanup_socket(Socket)

def start_telemetry(app_state, gearbox_manager):
    threading.Thread(target=telemetry_thread, args=(app_state, gearbox_manager), daemon=True).start()

def shutdown(app_state):
    global running
    running = False
    save_car_settings(app_state)

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        if 'drive_mode' in request.form:
            try:
                app_state.drive_mode = int(request.form['drive_mode'])
                app_state.manual_sport_mode = (app_state.drive_mode == 3)
                logger.info(f"Zmieniono tryb jazdy na: {DRIVE_MODE_LABELS.get(app_state.drive_mode, 'Unknown')}")
            except ValueError:
                logger.error(f"Nieprawidłowa wartość trybu jazdy: {request.form['drive_mode']}")
                app_state.drive_mode = 2
                app_state.manual_sport_mode = False
    with app_state.lock:
        data = app_state.get_telemetry()
    return render_template('index.html', telemetry=data, drive_mode=app_state.drive_mode, presets=app_state.presets, port=current_port)

@app.route('/dashboard')
def dashboard():
    with app_state.lock:
        data = app_state.get_telemetry()
    return render_template('dashboard.html', telemetry=data, drive_mode=app_state.drive_mode, current_car_id=app_state.current_car_id, car_settings=app_state.car_settings)

@app.route('/audi')
def audi():
    with app_state.lock:
        data = app_state.get_telemetry()
    return render_template('audi.html', telemetry=data, drive_mode=app_state.drive_mode, current_car_id=app_state.current_car_id, car_settings=app_state.car_settings)

@app.route('/telemetry')
def get_telemetry():
    with app_state.lock:
        data = app_state.get_telemetry()
        data['drive_mode'] = app_state.drive_mode
        data['drive_mode_label'] = DRIVE_MODE_LABELS.get(app_state.drive_mode, "Unknown")
        data['last_shift_time'] = float(app_state.last_shift_time)
        data['current_car_id'] = app_state.current_car_id
    return jsonify(data)

@app.route('/update_rpm', methods=['POST'])
def update_rpm():
    if request.remote_addr != '127.0.0.1':
        abort(403)
    try:
        car_id = request.form.get('car_id', app_state.current_car_id)
        mode = int(request.form.get('mode', app_state.drive_mode))
        shift_up = int(request.form['shift_up'])
        shift_down = int(request.form['shift_down'])
        shift_up_cooldown = float(request.form['shift_up_cooldown'])
        shift_down_cooldown = float(request.form['shift_down_cooldown'])

        if not (0 < shift_up < 10000 and 0 < shift_down < 10000):
            return jsonify({"status": "error", "message": "Nieprawidłowe wartości RPM"}), 400
        if not (0 <= shift_up_cooldown <= 10 and 0 <= shift_down_cooldown <= 10):
            return jsonify({"status": "error", "message": "Nieprawidłowe wartości cooldown"}), 400
    except (KeyError, ValueError):
        return jsonify({"status": "error", "message": "Nieprawidłowe dane wejściowe"}), 400

    if car_id not in app_state.car_settings:
        app_state.car_settings[car_id] = copy.deepcopy(DRIVE_MODES_DEFAULT)

    app_state.car_settings[car_id][mode] = {
        "shift_up": shift_up,
        "shift_down": shift_down,
        "shift_up_cooldown": shift_up_cooldown,
        "shift_down_cooldown": shift_down_cooldown
    }

    logger.info(f"Zaktualizowano ustawienia dla auta {car_id}, tryb {mode}: shift_up={shift_up}, shift_down={shift_down}")
    app_state.settings_save_queue.put((car_id, copy.deepcopy(app_state.car_settings[car_id])))
    return jsonify({"status": "ok", "shift_up": shift_up, "shift_down": shift_down, "shift_up_cooldown": shift_up_cooldown, "shift_down_cooldown": shift_down_cooldown})

@app.route('/save_preset', methods=['POST'])
def save_preset():
    if request.remote_addr != '127.0.0.1':
        abort(403)
    car_id = request.form.get('car_id', app_state.current_car_id)
    preset_name = request.form.get('preset_name')

    if not preset_name:
        return jsonify({"status": "error", "message": "Nazwa presetu jest wymagana"}), 400

    if car_id not in app_state.car_settings:
        app_state.car_settings[car_id] = copy.deepcopy(DRIVE_MODES_DEFAULT)

    app_state.presets[preset_name] = copy.deepcopy(app_state.car_settings[car_id])
    save_presets(app_state)
    logger.info(f"Zapisano preset '{preset_name}' dla auta {car_id}")
    return jsonify({"status": "ok"})

@app.route('/load_preset', methods=['POST'])
def load_preset():
    if request.remote_addr != '127.0.0.1':
        abort(403)
    car_id = request.form.get('car_id', app_state.current_car_id)
    preset_name = request.form.get('preset_name')

    if not preset_name or preset_name not in app_state.presets:
        return jsonify({"status": "error", "message": "Preset nie istnieje"}), 400

    app_state.car_settings[car_id] = copy.deepcopy(app_state.presets[preset_name])
    save_car_settings(app_state)
    logger.info(f"Wczytano preset '{preset_name}' dla auta {car_id}")
    return jsonify({"status": "ok"})

@app.route('/car_settings')
def get_car_settings():
    with app_state.lock:
        return jsonify(app_state.car_settings)

@app.route('/status')
def get_status():
    return jsonify({
        "running": running,
        "port": current_port,
        "last_update": time.ctime(app_state.last_update_time)
    })

if __name__ == '__main__':
    app_state = AppState()
    gearbox_manager = GearboxManager(app_state)
    load_car_settings(app_state)
    load_presets(app_state)
    threading.Thread(target=save_settings_worker, args=(app_state,), daemon=True).start()
    atexit.register(shutdown, app_state)
    try:
        start_telemetry(app_state, gearbox_manager)
        logger.info("Uruchomiono wątek telemetryczny z WebSocket.")
        socketio.run(app, debug=True, host='0.0.0.0', port=5000, use_reloader=False)
    except KeyboardInterrupt:
        shutdown(app_state)
        logger.info("Zatrzymano aplikację ręcznie.")
    except Exception as e:
        logger.error(f"Błąd podczas uruchamiania aplikacji: {e}", exc_info=True)
        shutdown(app_state)