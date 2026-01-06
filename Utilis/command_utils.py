"""
Copyright (c) 2025-present
 
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at
 
  http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

NDTwin core contributors (as of January 15, 2026):
    Prof. Shie-Yuan Wang <National Yang Ming Chiao Tung University; CITI, Academia Sinica> 
    Ms. Xiang-Ling Lin <CITI, Academia Sinica>
    Mr. Po-Yu Juan <CITI, Academia Sinica>
    Mr. Tsu-Li Mou <CITI, Academia Sinica> 
    Mr. Zhen-Rong Wu <National Taiwan Normal University>
    Mr. Ting-En Chang <University of Wisconsin, Milwaukee>
    Mr. Yu-Cheng Chen <National Yang Ming Chiao Tung University>

Some tools for interactive_commands
"""

import os
import numpy as np
import re
from loguru import logger
from nornir.core.task import Task, Result
import paramiko
import time

_DURATION_KEY = "interval_duration(d/h/m/s)"

def _create_exp_dir(FLOW_DIR: str,start_time: str) -> str:
    """
    Create a unique directory for the flow experiment logs.

    """
    os.makedirs(FLOW_DIR, exist_ok=True)
    exp_dir = os.path.join(FLOW_DIR, f"{start_time}")
    os.makedirs(exp_dir, exist_ok=True)
    return exp_dir

def fast_random_choice(start_end, weight, size):
    """
    Fast random choice function that samples uniformly from intervals.
    """
    idx = np.random.choice(len(start_end), size=size, p=weight)
    selected = start_end[idx]
    # Vectorized random integers for each interval
    if len(selected.shape) == 2 and selected.shape[1] == 2:
        low = selected[:, 0]
        high = selected[:, 1]
        return np.random.uniform(low, high)
    else:
        return selected

def _parse_duration(interval: list):
    """
    Parse duration strings to seconds for each interval.
    
    Supported formats:
    - "5s" or "5sec" -> 5 seconds
    - "2m" or "2min" -> 120 seconds  
    - "1h" or "1hour" -> 3600 seconds
    - "30" -> 30 seconds (default unit)
    
    Args:
        interval (list): List of interval dictionaries containing 'duration' strings
        
    Returns:
        None: Modifies the interval list in-place, adding 'duration_seconds' field
    """
    
    # Time unit conversion table
    time_units = {
        's': 1, 'sec': 1, 'second': 1, 'seconds': 1,
        'm': 60, 'min': 60, 'minute': 60, 'minutes': 60,
        'h': 3600, 'hour': 3600, 'hours': 3600,
        'd': 86400, 'day': 86400, 'days': 86400
    }
    
    for idx, inter in enumerate(interval):
        duration_str = inter.get(_DURATION_KEY, '0s')
        
        # Regular expression to match number and optional unit
        # Examples: "8s", "2m", "1.5h", "30" (no unit means seconds)
        match = re.match(r'^(\d+(?:\.\d+)?)\s*([a-zA-Z]*)$', str(duration_str).strip())
        
        
        if match:
            value = float(match.group(1))
            unit = match.group(2).lower() or 's'  # Default to seconds if no unit
            
            if unit in time_units:
                duration_seconds = int(value * time_units[unit])
                inter['duration_seconds'] = duration_seconds
                logger.trace(f"Parsed duration '{duration_str}' -> {duration_seconds} seconds")
            else:
                return (idx, duration_str)
        else:
            return (idx, duration_str)
        
    return None

def _process_intervals(interval_list):
    """
    Process interval configuration and validate total duration.
    
    Args:
        interval_list (list): List of interval configurations
        
    Returns:
        list: Processed intervals with timing information
        int: Total configured time in seconds
    """
    if not interval_list:
        return [], 0

    # Parse all durations
    parse_res = _parse_duration(interval_list)
    if parse_res is not None:
        idx, bad_value = parse_res
        raise ValueError(f"Invalid duration format at interval {idx + 1}: '{bad_value}'")
    
    # Calculate cumulative timing
    current_time = 0
    processed_intervals = []
    
    for i, interval in enumerate(interval_list):
        interval_copy = interval.copy()
        interval_copy['start_time'] = current_time
        duration_seconds = interval_copy.get('duration_seconds')
        if duration_seconds is None:
            raise ValueError(f"Interval {i + 1} is missing parsed duration_seconds")
        interval_copy['end_time'] = current_time + duration_seconds
        
        # Ensure we don't exceed simulation time
        
        processed_intervals.append(interval_copy)
        current_time = interval_copy['end_time']
        
        logger.info(f"Interval {i+1}: {_format_duration(duration_seconds)} "
                   f"({interval_copy['start_time']}s - {interval_copy['end_time']}s)")
    
    total_configured_time = sum(i['duration_seconds'] for i in processed_intervals)
    
    return processed_intervals, total_configured_time



def _format_duration(seconds):
    """
    Convert seconds to human-readable duration string.
    
    Args:
        seconds (int): Duration in seconds
        
    Returns:
        str: Formatted duration string (e.g., "2m 30s", "1h 5m", "45s")
    """
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        minutes = seconds // 60
        remaining_seconds = seconds % 60
        if remaining_seconds == 0:
            return f"{minutes}m"
        else:
            return f"{minutes}m {remaining_seconds}s"
    elif seconds < 86400:
        hours = seconds // 3600
        remaining_minutes = (seconds % 3600) // 60
        remaining_seconds = seconds % 60
        result = f"{hours}h"
        if remaining_minutes > 0:
            result += f" {remaining_minutes}m"
        if remaining_seconds > 0:
            result += f" {remaining_seconds}s"
        return result
    else:
        days = seconds // 86400
        remaining_hours = (seconds % 86400) // 3600
        result = f"{days}d"
        if remaining_hours > 0:
            result += f" {remaining_hours}h"
        return result

def collect_api_info(task: Task) -> Result:
    """
    Task to collect and store API server information from inventory.
    """
    host_data = task.host.data

    api_info = {
        'worker_node_server': host_data.get('worker_node_server'),
        'on_site_hosts': host_data.get('on_site_hosts', {}),
    }
    
    return Result(
        host=task.host,
        result=api_info
    )

def check_probability_and_parameter(traffic:dict, traffic_type:int,file_type:str):

    FLOW_DIST_NAME = ["near","middle","far"]
    FLOW_TYPE_NAME = [
        "unlimited_size_unlimited_rate_unlimited_duration_tcp",
        "unlimited_size_unlimited_rate_limited_duration_tcp",
        "limited_size_unlimited_rate_tcp",
        "unlimited_size_limited_rate_unlimited_duration_tcp",
        "unlimited_size_limited_rate_limited_duration_tcp",
        "limited_size_limited_rate_tcp",
        "unlimited_size_unlimited_rate_unlimited_duration_udp",
        "unlimited_size_unlimited_rate_limited_duration_udp",
        "limited_size_unlimited_rate_udp",
        "unlimited_size_limited_rate_unlimited_duration_udp",
        "unlimited_size_limited_rate_limited_duration_udp",
        "limited_size_limited_rate_udp"
        ]
    
    FLOW_PARAMETER_FEATURES = [
        "limited",
        "unlimited"
    ]
    FLOW_PARAMETER_NAME=[
        "size",
        "rate",
        "duration"
    ]
    FLOW_PARAMETER = {
        "size(bytes)" : "KMG",
        "rate(bits)" : "KMG",
        "duration(sec)" : "int"
    }

    flows = traffic.get("flow_arrival_rate(flow/sec)", None) if traffic_type == 0 else traffic.get("fixed_flow_number", None)
    if flows is None:
        return "Missing flow parameter."
    
    if type(flows) not in [int]:
        return "Flow parameter must be an integer."
    
    if flows <= 0:
        return "Flow parameter must > 0."
    
    # flow distance probability check

    flow_dist_prob = traffic.get("flow_distance_probability", {})

    if flow_dist_prob is None:
        return "Missing flow distance probability."
    
    sum_prob = 0.0

    for flow_dist,prob in flow_dist_prob.items():
        if flow_dist not in FLOW_DIST_NAME:
            return f"Invalid flow distance name '{flow_dist}'"
        sum_prob += float(prob)

    if abs(sum_prob - 1.0) > 1e-8:
        return "Flow distance probabilities must sum to 1.0"
    
    # flow type probability check

    flow_type_prob = traffic.get("flow_type_probability", {})

    if flow_type_prob is None:
        return "Missing flow type probability."
    
    sum_prob = 0.0

    for flow_type,prob in flow_type_prob.items():
        if flow_type not in FLOW_TYPE_NAME:
            return f"Invalid flow type name '{flow_type}'"
        sum_prob += float(prob)
    if abs(sum_prob - 1.0) > 1e-8:
        return "Flow type probabilities must sum to 1.0"

    if file_type == "dist":
        return None
    
    # flow type parameter check

    flow_parameters = traffic.get("flow_parameters", {})

    if flow_parameters is None:
        return "Missing flow parameters."
    
    # Step 1: Check if all entry names (flow types) are valid
    for entry_name in flow_parameters.keys():
        if entry_name not in FLOW_TYPE_NAME:
            return f"Invalid flow type in flow_parameters: '{entry_name}'. Must be one of: {FLOW_TYPE_NAME}"
        
        # Check if entry name contains protocol type (tcp/udp)
        if "tcp" not in entry_name and "udp" not in entry_name:
            return f"Flow type '{entry_name}' must specify protocol type (tcp/udp)."
    
    # Step 2 & 3: Check entry values (parameters) and their formats
    for entry_name, entry_value in flow_parameters.items():
        if not isinstance(entry_value, dict):
            return f"Flow parameters for '{entry_name}' must be a dictionary."
        
        # Parse the entry name to determine which parameters are expected
        expected_params = set()
        if "limited_size" in entry_name and "unlimited_size" not in entry_name:
            expected_params.add("size(bytes)")
        if "limited_rate" in entry_name and "unlimited_rate" not in entry_name:
            expected_params.add("rate(bits)")
        if "limited_duration" in entry_name and "unlimited_duration" not in entry_name and traffic_type == 0:
            expected_params.add("duration(sec)")
        
        # Validate each parameter in the entry value
        for param_name, param_value in entry_value.items():
            # Check if parameter name is valid
            valid_param = False
            param_restriction = None
            for param_val_name, restriction in FLOW_PARAMETER.items():
                if param_val_name in param_name:
                    valid_param = True
                    param_restriction = restriction
                    break
            
            if not valid_param:
                return f"Invalid parameter '{param_name}' in flow type '{entry_name}'. Valid parameters: {list(FLOW_PARAMETER.keys())}"
            
            # Check if this parameter should be present based on flow type name
            param_base = param_name.split('(')[0]  # Extract base name like 'size', 'rate', 'duration'
            should_be_present = False
            if param_base == "size" and "limited_size" in entry_name:
                should_be_present = True
            elif param_base == "rate" and "limited_rate" in entry_name:
                should_be_present = True
            elif param_base == "duration" and "limited_duration" in entry_name:
                should_be_present = True
            elif "limited_duration"  in entry_name and traffic_type == 1: # passed fixed flow number.
                should_be_present = True
            
            if not should_be_present:
                return f"Parameter '{param_name}' should not be present in '{entry_name}' (flow type does not specify limited_{param_base})"
            
            # Validate parameter value format
            if param_restriction == "KMG":
                match = re.match(r'^(\d+(?:\.\d+)?)([KMG]?)$', str(param_value).strip(), re.IGNORECASE)
                if not match:
                    return f"Invalid format for parameter '{param_name}' in '{entry_name}': '{param_value}'. Expected format: number with optional K/M/G suffix (e.g., '1M', '500K')"
            elif param_restriction == "int":
                if not isinstance(param_value, int) or param_value < 0:
                    return f"Parameter '{param_name}' in '{entry_name}' must be a non-negative integer, got: '{param_value}'"
        
        # Check if all expected parameters are present
        for expected_param in expected_params:
            
            found = any(expected_param in param_name for param_name in entry_value.keys())
            if not found:
                return f"Missing required parameter containing '{expected_param}' in flow type '{entry_name}'"
    
    return None

    


def file_checker(file_data : dict, file_type: str):
    """
    Check if the required files exist in the given file data dictionary.
    
    Args:
        file_data (dict): Dictionary containing content of the files to check.
    """
    if file_data.get("traffic_generator(iperf_or_iperf3)",None) == None:
        raise ValueError("Flow file missing 'traffic_generator(iperf_or_iperf3)' field.")
    else:
        if file_data["traffic_generator(iperf_or_iperf3)"] not in ["iperf","iperf3"]:
            raise ValueError("Flow file 'traffic_generator(iperf_or_iperf3)' field must be 'iperf' or 'iperf3'.")
        
    interval_list = file_data.get("intervals", None)

    if not interval_list:
        raise ValueError("Config file missing 'intervals' field.")

    processed_intervals, total_configured_time = _process_intervals(interval_list)

    for i, interval in enumerate(processed_intervals):
        traffics = [interval.get("varied_traffic", None), interval.get("fixed_traffic", None)]

        for traffic_type, traffic in enumerate(traffics):
            if traffic is None:
                continue
            error = check_probability_and_parameter(traffic, traffic_type,file_type)
            if error is not None:
                raise ValueError(f"Invalid traffic configuration at interval {i+1} with error {error}")

    return processed_intervals, total_configured_time, file_data.get("traffic_generator(iperf_or_iperf3)", "iperf3")
    
    
def get_startup_commands(task: Task) -> list:
    """
    Extract startup commands from the host's groups.
    """
    commands = []
    
    for group in task.host.groups:
        group_commands = group.data.get('startup_commands', [])
        if group_commands:
            commands.extend(group_commands)
    
    return commands

def get_shutdown_commands(task: Task) -> list:
    """
    Extract shutdown commands from the host's groups.
    """
    commands = []
    
    for group in task.host.groups:
        group_commands = group.data.get('shutdown_commands', [])
        if group_commands:
            commands.extend(group_commands)
    
    return commands

def execute_ssh_command(hostname, username, password, port, command, timeout=30):
    """
    Execute a single command via SSH using Paramiko.
    
    Args:
        hostname: Target host IP/hostname
        username: SSH username
        password: SSH password
        port: SSH port
        command: Command to execute
        timeout: Command timeout in seconds
    
    Returns:
        dict: Command result with output and status
    """
    ssh_client = None
    try:
        # Create SSH client
        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        # Connect to host
        ssh_client.connect(
            hostname=hostname,
            username=username,
            password=password,
            port=port,
            timeout=10,
            look_for_keys=False,
            allow_agent=False
        )
        
        # Execute command
        stdin, stdout, stderr = ssh_client.exec_command(command, timeout=timeout)
        
        # Get output
        exit_status = stdout.channel.recv_exit_status()
        output = stdout.read().decode('utf-8', errors='replace')
        error = stderr.read().decode('utf-8', errors='replace')
        
        return {
            'status': 'success' if exit_status == 0 else 'failed',
            'exit_code': exit_status,
            'output': output,
            'error': error
        }
        
    except paramiko.AuthenticationException as e:
        logger.error(f"Authentication failed for {hostname}: {e}")
        return {
            'status': 'error',
            'exit_code': -1,
            'output': '',
            'error': f'Authentication failed: {e}'
        }
    except paramiko.SSHException as e:
        logger.error(f"SSH error for {hostname}: {e}")
        return {
            'status': 'error',
            'exit_code': -1,
            'output': '',
            'error': f'SSH error: {e}'
        }
    except Exception as e:
        logger.error(f"Unexpected error for {hostname}: {e}")
        return {
            'status': 'error',
            'exit_code': -1,
            'output': '',
            'error': f'Error: {e}'
        }
    finally:
        if ssh_client:
            ssh_client.close()

def execute_command(task: Task,mode: int) -> Result:
    """
    Main task to execute startup commands on remote hosts via SSH using Paramiko.
    """
    # Get startup commands from groups
    commands = []
    env_setup = ""
    if mode == 1:
        commands = get_startup_commands(task)
        thread_cnt = task.host.data.get('thread_count', 500)
        retries = task.host.data.get('retries', 10)
        backoff_min_ms = task.host.data.get('backoff_min_ms', 250.0)
        backoff_max_ms = task.host.data.get('backoff_max_ms', 500.0)
        env_setup = f"export MAX_THREAD_CONCURRENCY={thread_cnt} && export RETRIES={retries} && export BACKOFF_MIN_MS={backoff_min_ms} && export BACKOFF_MAX_MS={backoff_max_ms} && "
        commands = [env_setup + cmd for cmd in commands]
    elif mode == 2:
        commands = get_shutdown_commands(task)
    
    if not commands:
        return Result(
            host=task.host,
            result={'status': 'no_commands', 'commands': []}
        )
    
    logger.trace(f"Executing {len(commands)} commands on {task.host.name}")
    
    # Get connection parameters
    hostname = task.host.hostname
    username = task.host.username
    password = task.host.password

    port = task.host.port if hasattr(task.host, 'port') else 22

    results = []
    
    # Execute each command
    for idx, command in enumerate(commands, 1):
        logger.trace(f"[{task.host.name}] Command {idx}/{len(commands)}: {command}")
        
        result = execute_ssh_command(
            hostname=hostname,
            username=username,
            password=password,
            port=port,
            command=command
        )
        
        results.append({
            'command': command,
            'output': result['output'],
            'error': result['error'],
            'exit_code': result['exit_code'],
            'status': result['status']
        })
        
        if result['status'] == 'success':
            logger.info(f"[{task.host.name}] Command completed successfully")
        else:
            logger.error(f"[{task.host.name}] Command failed: {result['error']}")
        
        # Small delay between commands
        time.sleep(0.1)
    
    return Result(
        host=task.host,
        result={
            'status': 'completed',
            'total_commands': len(commands),
            'commands': results
        }
    )



if __name__ == "__main__":
    pass
    