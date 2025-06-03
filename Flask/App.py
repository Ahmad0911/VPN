from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
import subprocess
import os
import threading
import time
import signal
import socket
import platform

app = Flask(__name__)
app.secret_key = os.urandom(24)  # Needed for flash messages

# Path to your .ovpn file
CONFIG_PATH = "/Users/apple/Desktop/Sample Tunnelblick VPN Configuration/vpnconfig.ovpn"

# Global variables
vpn_process = None
status_thread = None
vpn_status = "Disconnected"
vpn_output = []
stop_status_thread = False
connection_details = {
    "start_time": None,
    "public_ip": "Unknown",
    "location": "Unknown",
    "error": None
}

def get_system_info():
    """Get system information for debugging"""
    system_info = {
        "os": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "architecture": platform.machine(),
        "hostname": socket.gethostname(),
    }
    return system_info

def check_ovpn_file():
    """Check if the OpenVPN config file exists and is accessible"""
    if not os.path.exists(CONFIG_PATH):
        return False, f"Config file not found at: {CONFIG_PATH}"
    
    if not os.access(CONFIG_PATH, os.R_OK):
        return False, f"Config file is not readable at: {CONFIG_PATH}"
    
    return True, "Config file is accessible"

def check_openvpn_installed():
    """Check if OpenVPN is installed and accessible"""
    try:
        result = subprocess.run(['which', 'openvpn'], capture_output=True, text=True)
        if result.returncode == 0:
            return True, f"OpenVPN found at: {result.stdout.strip()}"
        return False, "OpenVPN not found in PATH"
    except Exception as e:
        return False, f"Error checking OpenVPN: {str(e)}"

def get_current_ip():
    """Get the current public IP address"""
    try:
        # This is a simple way to get IP - in production use a more reliable method
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except:
        return "Unknown"

def run_vpn(username=None, password=None):
    global vpn_process, vpn_status, vpn_output, connection_details
    
    # Reset output log
    vpn_output.clear()
    connection_details["error"] = None
    
    # Check if OpenVPN is installed
    installed, message = check_openvpn_installed()
    if not installed:
        vpn_output.append(f"ERROR: {message}")
        vpn_status = "Error"
        connection_details["error"] = message
        return
    
    # Check if config file exists
    exists, message = check_ovpn_file()
    if not exists:
        vpn_output.append(f"ERROR: {message}")
        vpn_status = "Error"
        connection_details["error"] = message
        return
    
    # Add initial system information to logs
    system_info = get_system_info()
    vpn_output.append(f"System: {system_info['os']} {system_info['release']} ({system_info['architecture']})")
    vpn_output.append(f"Current IP before connection: {get_current_ip()}")
    vpn_output.append(f"Using config file: {CONFIG_PATH}")
    
    # Create command - use credentials if provided

    cmd = ['sudo', 'openvpn', '--config', CONFIG_PATH]
    
    auth_file = None
    if username and password:
        auth_file = "/tmp/vpn_auth.txt"
        try:
            with open(auth_file, "w") as f:
                f.write(f"{username}\n{password}")
            os.chmod(auth_file, 0o600)  # Secure the file
            cmd.extend(['--auth-user-pass', auth_file])
            vpn_output.append("Using provided authentication credentials")
        except Exception as e:
            vpn_output.append(f"Error creating auth file: {str(e)}")
            vpn_status = "Error"
            connection_details["error"] = f"Auth file error: {str(e)}"
            return
    
    # Add verbosity for debugging
    cmd.append('--verb')
    cmd.append('4')
    
    vpn_output.append(f"Starting OpenVPN with command: {' '.join(cmd)}")
    vpn_status = "Connecting..."
    
    try:
        # Print a reminder about sudo password
        vpn_output.append("NOTE: OpenVPN requires admin privileges. You may need to enter your sudo password in the terminal where the Flask app is running.")
        
        vpn_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            bufsize=1
        )
        
        vpn_output.append(f"Process started with PID: {vpn_process.pid if vpn_process else 'None'}")
        
        # Update status based on output
        for line in iter(vpn_process.stdout.readline, ''):
            vpn_output.append(line.strip())
            
            # Limit the size of the output log
            if len(vpn_output) > 200:
                vpn_output.pop(0)
                
            # Look for common connection success messages
            if "Initialization Sequence Completed" in line:
                vpn_status = "Connected"
                connection_details["start_time"] = time.time()
                connection_details["public_ip"] = get_current_ip()
                vpn_output.append(f"Connected! New IP: {connection_details['public_ip']}")
            
            # Look for common error messages
            elif "AUTH_FAILED" in line:
                vpn_status = "Error"
                connection_details["error"] = "Authentication failed. Please check your username and password."
            elif "Cannot open TUN/TAP dev" in line:
                vpn_status = "Error"
                connection_details["error"] = "Cannot open TUN/TAP device. May need admin privileges."
            elif "Cannot allocate TUN/TAP dev dynamically" in line:
                vpn_status = "Error"
                connection_details["error"] = "Cannot allocate TUN/TAP device. May need admin privileges."
            elif "command failed" in line.lower():
                vpn_status = "Error"
                connection_details["error"] = "Command failed. Check permissions and configuration."
            
          
                     # Break if process exited
            if vpn_process is not None and vpn_process.poll() is not None:
                 break
        # Check if process exited without expected output
        if vpn_status == "Connecting...":
            vpn_status = "Error"
            connection_details["error"] = f"Process exited unexpectedly with code: {vpn_process.returncode}"
            vpn_output.append(f"Process exited with code: {vpn_process.returncode}")
        
        # Clean up auth file if it was created
        if auth_file and os.path.exists(auth_file):
            try:
                os.remove(auth_file)
                vpn_output.append("Auth file removed")
            except Exception as e:
                vpn_output.append(f"Warning: Could not remove auth file: {str(e)}")
            
        vpn_status = "Disconnected" if vpn_status != "Error" else "Error"
        vpn_process = None
        
    except Exception as e:
        vpn_output.append(f"Error: {str(e)}")
        vpn_status = "Error"
        connection_details["error"] = str(e)
        vpn_process = None

def monitor_vpn_status():
    global vpn_status, stop_status_thread, connection_details
    
    while not stop_status_thread:
        # Check if process is still running
        if vpn_process and vpn_process.poll() is None:
            # Process exists and is running
            if vpn_status != "Connected" and vpn_status != "Error" and len(vpn_output) > 5:
                # If we have output but no confirmed connection after some time
                vpn_status = "Connecting..."
        else:
            # No process running
            if vpn_status != "Error":
                vpn_status = "Disconnected"
            
        time.sleep(1)

@app.route('/')
def index():
    # Run initial checks to display status
    system_check = {
        "openvpn": check_openvpn_installed(),
        "config": check_ovpn_file(),
        "system": get_system_info()
    }
    
    return render_template('index.html', 
                           status=vpn_status, 
                           logs=vpn_output, 
                           system_check=system_check,
                           connection_details=connection_details)

@app.route('/connect', methods=['POST'])
def connect_vpn():
    global vpn_process, status_thread, stop_status_thread, vpn_status, connection_details
    
    if vpn_process:
        flash("VPN is already running")
        return redirect(url_for('index'))
    
    # Reset connection details
    connection_details = {
        "start_time": None,
        "public_ip": "Unknown",
        "location": "Unknown",
        "error": None
    }
    
    # Get credentials if provided
    username = request.form.get('username', '')
    password = request.form.get('password', '')
    
    # Start the VPN in a separate thread
    vpn_status = "Connecting..."
    vpn_thread = threading.Thread(target=run_vpn, args=(username, password))
    vpn_thread.daemon = True
    vpn_thread.start()
    
    # Start the status monitor if not already running
    if not status_thread or not status_thread.is_alive():
        stop_status_thread = False
        status_thread = threading.Thread(target=monitor_vpn_status)
        status_thread.daemon = True
        status_thread.start()
    
    flash("Connecting to VPN...")
    return redirect(url_for('index'))

@app.route('/disconnect', methods=['POST'])
def disconnect_vpn():
    global vpn_process, vpn_status, connection_details
    
    if vpn_process:
        vpn_output.append("Disconnecting VPN...")
        
        # Try to terminate the process gracefully
        vpn_process.terminate()
        
        # Give it some time to terminate
        time.sleep(2)
        
        # If still running, force kill
        if vpn_process.poll() is None:
            vpn_process.kill()
            vpn_output.append("Force killed OpenVPN process")
        else:
            vpn_output.append("Gracefully terminated OpenVPN process")
            
        vpn_status = "Disconnected"
        connection_details["start_time"] = None
        flash("VPN disconnected")
    else:
        flash("VPN is not connected")
        
    return redirect(url_for('index'))

@app.route('/status')
def status():
    """API endpoint for AJAX status updates"""
    uptime = None
    if connection_details["start_time"]:
        uptime = int(time.time() - connection_details["start_time"])
    
    return jsonify({
        "status": vpn_status,
        "logs": vpn_output[-15:],  # Return last 15 log entries
        "connection": {
            "uptime": uptime,
            "ip": connection_details["public_ip"],
            "location": connection_details["location"],
            "error": connection_details["error"]
        }
    })

@app.route('/check_system')
def check_system():
    """API endpoint for system diagnostics"""
    return jsonify({
        "openvpn": check_openvpn_installed(),
        "config": check_ovpn_file(),
        "system": get_system_info(),
        "current_ip": get_current_ip()
    })

def cleanup():
    global stop_status_thread
    
    # Stop the monitoring thread
    stop_status_thread = True
    if status_thread and status_thread.is_alive():
        status_thread.join(timeout=2)
    
    # Kill the VPN process if running
    if vpn_process and vpn_process.poll() is None:
        vpn_process.terminate()
        time.sleep(1)
        if vpn_process.poll() is None:
            vpn_process.kill()

# Register cleanup handler for graceful shutdown
def signal_handler(sig, frame):
    cleanup()
    os._exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

if __name__ == '__main__':
    print("Starting VPN Controller")
    print("NOTE: This application requires root/sudo privileges to run OpenVPN")
    print(f"Config file: {CONFIG_PATH}")
    print("OpenVPN status:", check_openvpn_installed())
    print("Config file status:", check_ovpn_file())
    print("Visit http://127.0.0.1:5000 in your browser to access the interface")
    
    try:
        app.run(debug=True, host='127.0.0.1', port=5000, use_reloader=False)
    finally:
        cleanup()

