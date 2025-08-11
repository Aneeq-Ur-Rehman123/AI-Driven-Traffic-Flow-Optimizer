from flask import Flask, render_template, Response, request, jsonify
import cv2
from ultralytics import YOLO
import threading
import time
import json
import serial
import serial.tools.list_ports

app = Flask(__name__)

# Load YOLOv8 model
model = YOLO('yolov8n.pt')

# Webcam configurations
camera_indices = {1: 0, 2: 1, 3: 2, 4: 3}

# Vehicle weights
vehicle_weights = {
    "car": 1,
    "truck": 3,
    "van": 2,
    "bike": 0.5,
    "stop sign": 2,
    "bus": 3
}

# COCO class IDs mapping
coco_classes = {
    2: "car",
    3: "motorbike",
    5: "bus",
    7: "truck",
    9: "traffic light",
    11: "stop sign"
}

# Traffic data storage
traffic_data = {
    "lane1": {"emergency": False, "remaining_time": 45, "light": "red", "vehicle_count": 0, "weight": 0, "next_state": None},
    "lane2": {"emergency": False, "remaining_time": 30, "light": "red", "vehicle_count": 0, "weight": 0, "next_state": None},
    "lane3": {"emergency": False, "remaining_time": 15, "light": "red", "vehicle_count": 0, "weight": 0, "next_state": None},
    "lane4": {"emergency": False, "remaining_time": 0, "light": "green", "vehicle_count": 0, "weight": 0, "next_state": None}
}

# System settings
system_settings = {
    "mode": "auto",
    "cycle_duration": 15,
    "yellow_duration": 3,
    "weight_threshold": 6
}

# Arduino serial connection
arduino = None
try:
    ports = serial.tools.list_ports.comports()
    arduino_port = ports[2].device if ports else None
    if arduino_port:
        arduino = serial.Serial(arduino_port, 9600, timeout=1)
        time.sleep(2)
        print(f"Connected to Arduino on port {arduino_port}")
except Exception as e:
    print(f"Arduino connection error: {str(e)}")

def send_to_arduino(lane, signal):
    if arduino and arduino.is_open:
        try:
            message = f"{lane}:{signal}\n"
            arduino.write(message.encode())
            print(f"Sent to Arduino: {message.strip()}")
        except Exception as e:
            print(f"Error sending to Arduino: {str(e)}")

def detect_objects(camera_id):
    cap = cv2.VideoCapture(camera_indices[camera_id])
    if not cap.isOpened():
        print(f"Error: Could not open camera {camera_id}")
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    while True:
        ret, frame = cap.read()
        if not ret:
            print(f"Error reading frame from camera {camera_id}")
            break

        try:
            results = model(frame)
            vehicle_counts = {"car": 0, "truck": 0, "van": 0, "bike": 0, "stop sign": 0, "bus": 0}
            emergency_detected = False

            for result in results:
                for box in result.boxes:
                    class_id = int(box.cls)
                    class_name = coco_classes.get(class_id, "")

                    if class_name == "stop sign":
                        emergency_detected = True
                        vehicle_counts["stop sign"] += 1
                    elif class_name in vehicle_weights:
                        vehicle_counts[class_name] += 1

            weight = sum(vehicle_weights[vt] * vehicle_counts[vt] for vt in vehicle_weights)

            with threading.Lock():
                traffic_data[f"lane{camera_id}"].update({
                    "emergency": emergency_detected,
                    "vehicle_count": sum(vehicle_counts.values()),
                    "weight": weight
                })
                print(f"Lane {camera_id} - Weight: {weight}, Emergency: {emergency_detected}")

            annotated_frame = results[0].plot()
            ret, buffer = cv2.imencode('.jpg', annotated_frame)
            if ret:
                yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')

        except Exception as e:
            print(f"Error processing camera {camera_id}: {str(e)}")
            continue

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/video_feed/<int:camera_id>')
def video_feed(camera_id):
    return Response(detect_objects(camera_id), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/traffic_data')
def get_traffic_data():
    try:
        update_traffic_lights()
        return jsonify(traffic_data)
    except Exception as e:
        print(f"Error generating traffic data: {str(e)}")
        return jsonify({"error": "Server error"}), 500

@app.route('/update_settings', methods=['POST'])
def update_settings():
    try:
        data = request.get_json()
        system_settings['mode'] = data.get('mode', system_settings['mode'])
        system_settings['cycle_duration'] = int(data.get('duration', system_settings['cycle_duration']))
        system_settings['yellow_duration'] = int(data.get('yellow_duration', system_settings['yellow_duration']))
        # Reset all timers when settings change
        current_green = next((k for k, v in traffic_data.items() if v["light"] == "green"), "lane4")
        lanes_order = ["lane1", "lane2", "lane3", "lane4"]
        current_idx = lanes_order.index(current_green)
        
        for i, lane_id in enumerate(lanes_order):
            position = (i - current_idx) % 4
            if position == 0:
                traffic_data[lane_id].update({
                    "light": "green",
                    "remaining_time": system_settings['cycle_duration'],
                    "next_state": None
                })
            else:
                traffic_data[lane_id].update({
                    "light": "red",
                    "remaining_time": position * system_settings['cycle_duration'],
                    "next_state": None
                })
        
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

def calculate_red_time(lane_id):
    """Calculate accurate remaining red time for a lane based on current signal cycle"""
    lanes_order = ["lane1", "lane2", "lane3", "lane4"]
    
    # Find current green lane and its remaining time
    green_lane = next((lane for lane in traffic_data.values() if lane["light"] == "green"), None)
    
    if not green_lane:
        return 0  # Shouldn't happen in normal operation
    
    green_lane_id = next(k for k, v in traffic_data.items() if v == green_lane)
    green_idx = lanes_order.index(green_lane_id)
    current_idx = lanes_order.index(lane_id)
    
    # Calculate position in the rotation (0=current green, 1=next, etc.)
    position = (current_idx - green_idx) % 4
    
    if position == 0:
        return 0  # This is the green lane
    elif position == 1:
        # Next in rotation: remaining green + yellow
        return green_lane["remaining_time"] + system_settings["yellow_duration"]
    elif position == 2:
        # Two positions away: remaining green + yellow + next lane's full cycle
        return (green_lane["remaining_time"] + system_settings["yellow_duration"] + 
                system_settings["cycle_duration"])
    elif position == 3:
        # Three positions away: remaining green + yellow + next 2 lanes' cycles
        return (green_lane["remaining_time"] + system_settings["yellow_duration"] + 
                system_settings["cycle_duration"] * 2)

def update_traffic_lights():
    with threading.Lock():
        # First decrement all timers every second
        for lane_id, lane in traffic_data.items():
            if lane["remaining_time"] > 0:
                lane["remaining_time"] -= 1
            elif lane["light"] == "red":
                # For red lights, recalculate remaining time
                lane["remaining_time"] = calculate_red_time(lane_id)

        # Handle state transitions
        for lane_id, lane in traffic_data.items():
            if lane["remaining_time"] <= 0:
                if lane["next_state"]:
                    # Transition to next state
                    lane["light"] = lane["next_state"]
                    lane["next_state"] = None
                    send_to_arduino(lane_id[-1], lane["light"])
                    
                    # Set new remaining time
                    if lane["light"] == "green":
                        lane["remaining_time"] = system_settings["cycle_duration"]
                    elif lane["light"] == "yellow":
                        lane["remaining_time"] = system_settings["yellow_duration"]
                    else:  # red
                        lane["remaining_time"] = calculate_red_time(lane_id)

        # Emergency handling
        emergency_lane = None
        for lane_id, lane in traffic_data.items():
            if lane["emergency"]:
                emergency_lane = lane_id
                break
        
        if emergency_lane:
            print(f"Emergency detected in {emergency_lane} - giving priority")
            for lane_id, lane in traffic_data.items():
                if lane_id == emergency_lane:
                    if lane["light"] != "green":
                        if lane["light"] == "red":
                            lane["light"] = "yellow"
                            lane["next_state"] = "green"
                            lane["remaining_time"] = system_settings["yellow_duration"]
                            send_to_arduino(lane_id[-1], "yellow")
                        elif lane["light"] == "yellow":
                            lane["next_state"] = "green"
                else:
                    if lane["light"] == "green":
                        lane["light"] = "yellow"
                        lane["next_state"] = "red"
                        lane["remaining_time"] = system_settings["yellow_duration"]
                        send_to_arduino(lane_id[-1], "yellow")
                    elif lane["light"] == "yellow" and lane["next_state"] != "red":
                        lane["next_state"] = "red"
            return
        
        # Weight-based priority
        high_weight_lanes = {k: v for k, v in traffic_data.items() 
                           if v["weight"] > system_settings["weight_threshold"]}
        
        if high_weight_lanes:
            max_lane = max(high_weight_lanes.items(), key=lambda x: x[1]["weight"])[0]
            print(f"High weight detected in {max_lane} - giving priority")
            
            for lane_id, lane in traffic_data.items():
                if lane_id == max_lane:
                    if lane["light"] != "green":
                        if lane["light"] == "red":
                            lane["light"] = "yellow"
                            lane["next_state"] = "green"
                            lane["remaining_time"] = system_settings["yellow_duration"]
                            send_to_arduino(lane_id[-1], "yellow")
                        elif lane["light"] == "yellow":
                            lane["next_state"] = "green"
                else:
                    if lane["light"] == "green":
                        lane["light"] = "yellow"
                        lane["next_state"] = "red"
                        lane["remaining_time"] = system_settings["yellow_duration"]
                        send_to_arduino(lane_id[-1], "yellow")
                    elif lane["light"] == "yellow" and lane["next_state"] != "red":
                        lane["next_state"] = "red"
            return
        
        # Normal operation
        if any(lane["light"] == "yellow" for lane in traffic_data.values()):
            return
        
        current_green = next((k for k, v in traffic_data.items() if v["light"] == "green"), None)
        
        if current_green:
            if traffic_data[current_green]["remaining_time"] > 0:
                return
            
            # Current green time expired, switch to yellow
            traffic_data[current_green].update({
                "light": "yellow",
                "next_state": "red",
                "remaining_time": system_settings["yellow_duration"]
            })
            send_to_arduino(current_green[-1], "yellow")
            
            # Prepare next lane to go green
            lanes_order = ["lane1", "lane2", "lane3", "lane4"]
            current_index = lanes_order.index(current_green)
            next_index = (current_index + 1) % len(lanes_order)
            next_green = lanes_order[next_index]
            
            traffic_data[next_green].update({
                "light": "yellow",
                "next_state": "green",
                "remaining_time": system_settings["yellow_duration"]
            })
            send_to_arduino(next_green[-1], "yellow")
        else:
            # Initial state - start with lane4
            traffic_data["lane4"].update({
                "light": "yellow",
                "next_state": "green",
                "remaining_time": system_settings["yellow_duration"]
            })
            send_to_arduino("4", "yellow")

if __name__ == '__main__':
    for cam_id, cam_idx in camera_indices.items():
        cap = cv2.VideoCapture(cam_idx)
        if cap.isOpened():
            print(f"Camera {cam_id} (index {cam_idx}) is working")
            cap.release()
        else:
            print(f"Warning: Could not open camera {cam_id} (index {cam_idx})")
    
    app.run(host='0.0.0.0', port=5000, threaded=True, debug=True)