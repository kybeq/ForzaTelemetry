$(document).ready(function() {
    // Initial state
    let currentDriveMode = 2;
    let currentCarId = "1";
    let lastTelemetryData = {};

    // Telemetry fields
    const keyTelemetryFields = [
        'CarPerformanceIndex', 'CurrentRPM', 'Speed', 'Gear', 'drive_mode',
        'shift_up', 'shift_down', 'shift_up_cooldown', 'shift_down_cooldown'
    ];
    const additionalTelemetryFields = [
        'RaceOn', 'DrivetrainType', 'RpmHigher', 'RpmLower', 'LastShiftTime', 'TimeStamp',
        'Power', 'Torque', 'Throttle', 'Brake', 'Clutch', 'HandBrake', 'Steer',
        'DistanceTraveled', 'BestLap', 'LastLap', 'CurrentLap', 'CurrentRaceTime', 'LapNumber', 'RacePosition'
    ];

    // Initialize tire temperature chart
    const ctx = document.getElementById('tire-temperature-chart').getContext('2d');
    const tireTempChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                { label: 'Front Left', data: [], borderColor: '#00b4d8', backgroundColor: 'rgba(0, 180, 216, 0.1)', fill: true, tension: 0.4, borderWidth: 2 },
                { label: 'Front Right', data: [], borderColor: '#00d4a8', backgroundColor: 'rgba(0, 212, 168, 0.1)', fill: true, tension: 0.4, borderWidth: 2 },
                { label: 'Rear Left', data: [], borderColor: '#ff6d00', backgroundColor: 'rgba(255, 109, 0, 0.1)', fill: true, tension: 0.4, borderWidth: 2 },
                { label: 'Rear Right', data: [], borderColor: '#ffd60a', backgroundColor: 'rgba(255, 214, 10, 0.1)', fill: true, tension: 0.4, borderWidth: 2 }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: { title: { display: true, text: 'Time' }, ticks: { color: '#e9ecef' }, grid: { color: '#343a40' } },
                y: { 
                    title: { display: true, text: 'Temperature (°C)' }, 
                    ticks: { color: '#e9ecef' }, 
                    grid: { color: '#343a40' },
                    suggestedMin: 0,
                    suggestedMax: 100
                }
            },
            plugins: { legend: { labels: { color: '#e9ecef' } } },
            animation: false
        }
    });

    // WebSocket connection
    const socket = io('http://localhost:5000');
    socket.on('connect', function() {
        console.log('Connected to WebSocket');
    });

    socket.on('telemetry_update', function(data) {
        console.log('Received WebSocket Telemetry:', data);

        if (Object.keys(data).length > 0) {
            lastTelemetryData = data;
        }

        const displayData = lastTelemetryData;

        $('#key-car-performance-index').text(displayData.CarPerformanceIndex || '0');
        $('#key-current-rpm').text(displayData.CurrentRPM || '0');
        $('#key-max-rpm').text(displayData.MaxRPM || '0');
        $('#key-idle-rpm').text(displayData.IdleRPM || '0');
        $('#key-speed').text(Math.floor(displayData.Speed || 0));

        // Deklaracja gearPrefix przed użyciem
        const gearPrefix = displayData.drive_mode == '1' ? 'E' : 
                        displayData.drive_mode == '2' ? 'D' : 
                        displayData.drive_mode == '3' ? 'S' : 
                        displayData.drive_mode == '4' ? 'M' : 'S';
        $('#key-gear').html(`<span class="drive-modes">${gearPrefix}</span>${displayData.Gear || '0'}`);
        // Obsługa biegu wstecznego
        const gearDisplay = displayData.Gear === -1 ? 'R' : (displayData.Gear || '0');
        $('#key-gear').html(`<span class="drive-modes">${gearPrefix}</span>${gearDisplay}`);
        
        $('#key-drive-mode').text(displayData.drive_mode || '2');
        $('#key-shift-up').text(displayData.shift_up || '7000');
        $('#key-shift-down').text(displayData.shift_down || '2500');
        $('#key-shift-up-cooldown').text(displayData.shift_up_cooldown || '1.5');
        $('#key-shift-down-cooldown').text(displayData.shift_down_cooldown || '1.5');

        let telemetryHtml = '';
        additionalTelemetryFields.forEach(key => {
            if (displayData[key] !== undefined) {
                telemetryHtml += `<tr><td>${key}</td><td>${displayData[key]}</td></tr>`;
            }
        });
        $('#additional-telemetry').html(telemetryHtml);

        currentCarId = displayData.current_car_id || currentCarId || '1';
        $('#current-car-id').text(currentCarId);
        $('#car-id').val(currentCarId).prop('disabled', true);

        if (!lastTelemetryData.hasOwnProperty('shift_up')) {
            $('#shift-up').val(displayData.shift_up || '7000');
            $('#shift-down').val(displayData.shift_down || '2500');
            $('#shift-up-cooldown').val(displayData.shift_up_cooldown || '1.5');
            $('#shift-down-cooldown').val(displayData.shift_down_cooldown || '1.5');
        }

        currentDriveMode = displayData.drive_mode || 2;
        $('.drive-mode-btn').removeClass('active-mode');
        $(`.drive-mode-btn[data-mode="${currentDriveMode}"]`).addClass('active-mode');
    });

    // Function to update tire temperature chart and spans
    function updateTireTempChart() {
        const displayData = lastTelemetryData;
        const now = new Date().toLocaleTimeString();
        const temps = {
            FL: Math.floor(displayData.TireTempFL || 0),
            FR: Math.floor(displayData.TireTempFR || 0),
            RL: Math.floor(displayData.TireTempRL || 0),
            RR: Math.floor(displayData.TireTempRR || 0)
        };
        console.log('Tire Temperatures for Chart:', temps);

        $('#tire-fl').text(temps.FL + ' °C');
        $('#tire-fr').text(temps.FR + ' °C');
        $('#tire-rl').text(temps.RL + ' °C');
        $('#tire-rr').text(temps.RR + ' °C');

        tireTempChart.data.labels.push(now);
        tireTempChart.data.datasets[0].data.push(temps.FL);
        tireTempChart.data.datasets[1].data.push(temps.FR);
        tireTempChart.data.datasets[2].data.push(temps.RL);
        tireTempChart.data.datasets[3].data.push(temps.RR);

        if (tireTempChart.data.labels.length > 50) {
            tireTempChart.data.labels.shift();
            tireTempChart.data.datasets.forEach(dataset => dataset.data.shift());
        }

        try {
            tireTempChart.update('none');
        } catch (error) {
            console.error('Error updating tire temp chart:', error);
        }
    }

    // Drive mode button click handler
    $('.drive-mode-btn').on('click', function() {
        const mode = parseInt($(this).data('mode'));
        $.post('/', { drive_mode: mode })
            .done(function() {
                console.log(`Drive mode changed to ${mode}`);
            })
            .fail(function(jqXHR, textStatus, errorThrown) {
                console.error('Error changing drive mode:', textStatus, errorThrown);
            });
    });

    // RPM settings form submission
    $('#rpm-form').on('submit', function(e) {
        e.preventDefault();
        const formData = {
            car_id: currentCarId,
            mode: currentDriveMode,
            shift_up: $('#shift-up').val(),
            shift_down: $('#shift-down').val(),
            shift_up_cooldown: $('#shift-up-cooldown').val(),
            shift_down_cooldown: $('#shift-down-cooldown').val()
        };

        $.post('/update_rpm', formData)
            .done(function(response) {
                if (response.status === 'ok') {
                    console.log('RPM settings updated:', response);
                    lastTelemetryData.shift_up = response.shift_up;
                    lastTelemetryData.shift_down = response.shift_down;
                    lastTelemetryData.shift_up_cooldown = response.shift_up_cooldown;
                    lastTelemetryData.shift_down_cooldown = response.shift_down_cooldown;
                } else {
                    alert('Failed to update RPM settings');
                }
            })
            .fail(function(jqXHR, textStatus, errorThrown) {
                console.error('Error updating RPM:', textStatus, errorThrown);
                alert('Error updating RPM settings');
            });
    });

    // Save preset button click handler
    $('#save-preset').on('click', function() {
        const presetName = $('#preset-name').val();
        if (!presetName) {
            alert('Please enter a preset name');
            return;
        }
        $.post('/save_preset', { preset_name: presetName, car_id: currentCarId })
            .done(function(response) {
                if (response.status === 'ok') {
                    console.log('Preset saved');
                    location.reload();
                } else {
                    alert(response.message);
                }
            })
            .fail(function(jqXHR, textStatus, errorThrown) {
                console.error('Error saving preset:', textStatus, errorThrown);
                alert('Error saving preset');
            });
    });

    // Load preset button click handler
    $('#load-preset').on('click', function() {
        const presetName = $('#preset-select').val();
        if (!presetName) {
            alert('Please select a preset');
            return;
        }
        $.post('/load_preset', { preset_name: presetName, car_id: currentCarId })
            .done(function(response) {
                if (response.status === 'ok') {
                    console.log('Preset loaded');
                } else {
                    alert(response.message);
                }
            })
            .fail(function(jqXHR, textStatus, errorThrown) {
                console.error('Error loading preset:', textStatus, errorThrown);
                alert('Error loading preset');
            });
    });

    // Initialization and refresh intervals
    setInterval(updateTireTempChart, 1000); // Tire temps every 1s (1 Hz)
});