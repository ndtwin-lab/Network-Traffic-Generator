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

Interactive Commands Module for Mininet Network Management

This module provides an asynchronous command-line interface for managing
Mininet networks, including dynamic traffic generation, host management,
and network testing capabilities built on top of Python's ``asyncio``
event loop.

"""
import time
from datetime import datetime
import random
import asyncio
import os
from typing import Any, Dict, List, Optional, Set, Tuple, Union

try:
    from mininet.cli import CLI
except ImportError:
    CLI = None

from loguru import logger
import json
import pandas as pd
import numpy as np
from Utilis.distance_seperate import distance_partition
from Utilis.command_utils import _create_exp_dir, fast_random_choice, collect_api_info, file_checker, execute_command
from Utilis.communicator import MininetCommunicator, APICommunicator
import sys

from nornir import InitNornir
from nornir.core.filter import F
from nornir.core import Nornir

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import NestedCompleter,PathCompleter,Completer,Completion
from prompt_toolkit.document import Document
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.patch_stdout import patch_stdout

# == logger config ==
def logger_config(level:str="DEBUG"):
    logger.remove(0)
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level} : {message}</level>",
        colorize=True,
        backtrace=True,
        diagnose=True,
        level = level
    )
# == prompt autocomplete setting ==

class ConcateCompleter(Completer):
    def __init__(self):
        self.path_completer = PathCompleter()
        self.completer =  NestedCompleter.from_nested_dict({
            "dist": {"--config":None},
            "flow": {"--config": None},
            "exit" : None,
        })
        self.config_start_position = -1
        

    def get_completions(self, document, complete_event):
        if ("--config" in document.text):
            if self.config_start_position == -1:
                self.config_start_position = document.text.find("--config") + len("--config") + 1
            sub_doc = Document(document.text[self.config_start_position:])
            yield from (Completion(completion.text, start_position=completion.start_position,display=completion.display) for completion in self.path_completer.get_completions(sub_doc, complete_event))
        else :
            self.config_start_position = -1  # Reset position for next command
            yield from (Completion(completion.text, start_position=completion.start_position,display=completion.display) for completion in self.completer.get_completions(document, complete_event))

# == prompt autocomplete setting ==


HOSTS = None
CONNECTIONS = {"near":[],"middle":[],"far":[]}

RUNNING = 0
UNLIMITED_FLOW = 0

LOCK = asyncio.Lock()
CMD_LOCK = asyncio.Lock()
RECYCLE_STOP_EVENT = asyncio.Event()

async def _on_flow_finished(t) -> None:
    global RUNNING
    async with LOCK:
        logger.debug(f"Flow finished, current: {RUNNING} decreasing running count by {t}")
        RUNNING -= t
        if RUNNING < 0:
            RUNNING = 0

INTERFACE = None
NTG_CONFIG = None

IPERF = "iperf3"  # check whether user want to use iperf3 or iperf2

DEFAULT_FLOW_DIR = './flow_logs' # prefix of place to store output file
FLOW_DIR = '' # place to store iperf output file, will automatically create unique dir
FILL_WIDTH = 0

PORT_DISCOVERY_CMD = (
    "python3 -c 'import socket; s = socket.socket(socket.AF_INET, socket.SOCK_STREAM); "
    "s.bind((\"\", 0)); print(s.getsockname()[1]); s.close()'"
)

DEFAULT_TYPE_PROBABILITY = {
    "unlimited_size_unlimited_rate_unlimited_duration_tcp":0.0,
    "unlimited_size_unlimited_rate_limited_duration_tcp":0.0,
    "limited_size_unlimited_rate_tcp":0.0,
    "unlimited_size_limited_rate_unlimited_duration_tcp":0.0,
    "unlimited_size_limited_rate_limited_duration_tcp":0.0,
    "limited_size_limited_rate_tcp":0.0,
    "unlimited_size_unlimited_rate_unlimited_duration_udp":0.0,
    "unlimited_size_unlimited_rate_limited_duration_udp":0.0,
    "limited_size_unlimited_rate_udp":0.0,
    "unlimited_size_limited_rate_unlimited_duration_udp":0.0,
    "unlimited_size_limited_rate_limited_duration_udp":0.0,
    "limited_size_limited_rate_udp":0.0
}

DEFAULT_DISTANCE_PROBABILITY = {"near":0.0,"middle":0.0,"far":0.0}

DEFAULT_FLOW_PARAMETERS = {
    "unlimited_size_unlimited_rate_unlimited_duration_tcp": ['-un'],
    "unlimited_size_unlimited_rate_limited_duration_tcp": ['-t'],
    "limited_size_unlimited_rate_tcp": ['-n'],
    "unlimited_size_limited_rate_unlimited_duration_tcp": ['-b','-un'],
    "unlimited_size_limited_rate_limited_duration_tcp": ['-b','-t'],
    "limited_size_limited_rate_tcp": ['-n','-b'],
    "unlimited_size_unlimited_rate_unlimited_duration_udp": ['-u','-un'],
    "unlimited_size_unlimited_rate_limited_duration_udp": ['-u','-t'],
    "limited_size_unlimited_rate_udp": ['-u','-n','-b'],
    "unlimited_size_limited_rate_unlimited_duration_udp": ['-u','-b','-un'],
    "unlimited_size_limited_rate_limited_duration_udp": ['-u','-b','-t'],
    "limited_size_limited_rate_udp": ['-u','-n','-b']
}

def get_hardware_server_info(filtered_nornir:Nornir) -> Optional[Dict[str,Any]]:
    api = set()
    hosts_name_map = {}

    hardware_info = filtered_nornir.run(task=collect_api_info)
    for host_name,result in hardware_info.items():
        if not result.failed:
            api_server = result.result.get('worker_node_server',None)
            api.add(api_server)
            on_site_hosts = result.result.get('on_site_hosts',{})
            for name,ip in on_site_hosts.items():
                hosts_name_map[name] = dict(ip=ip,api=api_server)
    
    ssh_result = filtered_nornir.run(task=execute_command,mode=1)

    for host_name,result in ssh_result.items():
        logger.debug(f"Startup result for host {host_name}: {result.result}")


    if len(api) !=0 and len(hosts_name_map) !=0:
        return dict(api_servers=list(api),hosts_name_map=hosts_name_map)
    else:
        return None

def interactive_command_mode(net,config_file_path:str="NTG.yaml"):
    """
    Interactive command processor for Mininet network management.
    
    This function provides a custom command-line interface that allows users to:
    - Create network links between hosts using iperf3
    - Manage host processes and xterm windows
    - Control network traffic generation
    - Gracefully exit and cleanup resources
    
    Args:
        net (Mininet): The Mininet network instance to manage
    
    Available Commands:
        exit: Quit the interactive mode and cleanup
    """
    # == Interactive CLI or Custom Command Mode ==
    # After the setup, we can either enter the Mininet CLI for interactive commands,
    # or we can run custom commands to start servers and clients, manage links, etc.
    global INTERFACE,NTG_CONFIG

    

    NTG_CONFIG = InitNornir(config_file=config_file_path)
    ndtwin_server = None
    try:
        if NTG_CONFIG.inventory.hosts.get("Mininet_Testbed") is not None:
            mininet_testbed = NTG_CONFIG.inventory.hosts["Mininet_Testbed"]
            mininet_mode = mininet_testbed.data.get("mode","cli")
            logger_config(level=mininet_testbed.data.get("log_level","DEBUG"))
            if mininet_mode == "cli":
                logger.info("Entering Mininet CLI mode...")
                CLI(net)
                return
            elif mininet_mode == "custom_command":

                sleep_time = mininet_testbed.data.get("sleep_time",{"min":0.5,"max":1.5})
                ndtwin_server = mininet_testbed.data.get("ndtwin_server",None)

                INTERFACE = MininetCommunicator(sleep_time=sleep_time, 
                                                logger=logger,
                                                on_flow_finished=_on_flow_finished)
                
        
        if NTG_CONFIG.inventory.hosts.get("Hardware_Testbed") is not None:

            hardware_testbed = NTG_CONFIG.inventory.hosts["Hardware_Testbed"]
            logger_config(level=hardware_testbed.data.get("log_level","DEBUG"))

            hardware_server = get_hardware_server_info(NTG_CONFIG.filter(F(groups__contains="worker_node_servers")))
            
            recycle_interval = hardware_testbed.data.get("recycle_interval",10)
            sleep_time = hardware_testbed.data.get("sleep_time",{"min":0.5,"max":1.5})
            ports_limitation = hardware_testbed.data.get("ports_limitation",{"min_port":5204,"max_port":16205,"exclude_ports":[8000]})
            ndtwin_server = hardware_testbed.data.get("ndtwin_server",None)

            if hardware_server is None:
                raise ValueError("Failed to get Hardware server information")
            
            INTERFACE = APICommunicator(sleep_time=sleep_time,
                                        logger=logger,
                                        on_flow_finished=_on_flow_finished,
                                        recycle_stop_event=RECYCLE_STOP_EVENT,
                                        hardware_server=hardware_server,
                                        recycle_interval=recycle_interval,
                                        ports_limitation=ports_limitation)
            
        elif INTERFACE is None:
            raise ValueError("Current NTG configuration is invalid. Please check your NTG.yaml file.")
        
        logger.info("Entering Custom Command mode...")
        link_relationship_init(ndtwin_server=ndtwin_server)
        asyncio.run(_run_custom_command_loop(net))

    except KeyboardInterrupt:
        return

def link_relationship_init(ndtwin_server=None):
    global CONNECTIONS,HOSTS,CMD_LOCK

    logger.info("Waiting to compute all link relationships...")
    try:
        while True:
            CONNECTIONS,HOSTS = distance_partition(ndtwin_server=ndtwin_server)
            if CONNECTIONS is False and HOSTS is False:
                logger.warning("Failed to get link relationships, retrying...")
                time.sleep(2)
            else:
                logger.info("Link relationships computed successfully.")
                logger.debug(f"Connection relationships: {HOSTS}")
                break
    except Exception as e:
        logger.warning(e)
        # === You can customize default connections here ===

        for i in range(32):
            CONNECTIONS['middle'].append({'src': {"name":f"h{i+1}"}, 'dst': {"name":f"h{(i+64)+1}"}})
            CONNECTIONS['far'].append({'src': {"name":f"h{i+1}"}, 'dst': {"name":f"h{(i+96)+1}"}})

        # =======================================================

    random.seed()

async def _run_custom_command_loop(net):
    """
    Internal function that runs the custom command processing loop.
    
    內部函式，運行自定義命令處理迴圈。
    
    Args:
        net (Mininet): The Mininet network instance
    """
    global FLOW_DIR
    session = PromptSession(history=InMemoryHistory(), completer=ConcateCompleter())

    try:
        with patch_stdout():
            while True:
                line = await session.prompt_async("NTG> ")

                parts = line.strip().split()
                if not parts:
                    continue

                cmd, *args = parts

                if cmd == 'exit':
                    await _handle_exit_command(leave=True)
                    break

                if cmd == 'flow':
                    try:
                        await _handle_flow_command(net, args)
                    except OSError as exc:
                        logger.warning(f"Experiment directory creation failed: {exc}")
                    continue

                if cmd == 'dist':
                    try:
                        await _handle_dist_command(net, args)
                    except OSError as exc:
                        logger.warning(f"Experiment directory creation failed: {exc}")
                    continue

                print(f"Unknown command: {cmd}")
                print("Available commands: exit, flow, dist")

    except KeyboardInterrupt:
        await _handle_exit_command(leave=True)


async def ending_process(net):
    global IPERF, RECYCLE_STOP_EVENT, RUNNING, UNLIMITED_FLOW, INTERFACE
    
    async with LOCK:
        running = RUNNING
        unlimited = UNLIMITED_FLOW

    while running > unlimited:
        logger.info(f"Waiting for all connections to be restored, currently running host pairs: {running} with {unlimited} unlimited duration flows")
        await asyncio.sleep(1)
        async with LOCK:
            running = RUNNING
            unlimited = UNLIMITED_FLOW
    
    logger.success("Experiment completed.")
    await _handle_exit_command("Experiment", show_msg=False)
    
    # Reset global state for next experiment
    IPERF = "iperf3"
    RUNNING = 0
    UNLIMITED_FLOW = 0

def probability_assignment(current_traffic_config,is_dynamic=True,type_to_parameter=False):
    """

    Calculate probabilitys for distance and type

    """
    # get distance and type probability and flow amounts.
    current_distance_probability = current_traffic_config.get('flow_distance_probability', DEFAULT_DISTANCE_PROBABILITY)
    current_type_probability = current_traffic_config.get('flow_type_probability', DEFAULT_TYPE_PROBABILITY)
   
    if is_dynamic:
        current_flow_amounts = current_traffic_config.get('flow_arrival_rate(flow/sec)', 1)
    else:
        current_flow_amounts = current_traffic_config.get('fixed_flow_number', 1)


    distance_names = [name for name,probability in current_distance_probability.items() if probability > 0.0]
    type_names = [name for name,probability in current_type_probability.items() if probability > 0.0]

    distance_random_choice = fast_random_choice(np.array(distance_names), [current_distance_probability[name] for name in distance_names], current_flow_amounts)
    type_random_choice  = fast_random_choice(np.array(type_names), [current_type_probability[name] for name in type_names], current_flow_amounts)
    
    distance_distribution = {name: np.sum(distance_random_choice == name) for name in distance_names}
    type_distribution = {name: np.sum(type_random_choice == name) for name in type_names}

    # Create lists for assignment - each element represents one connection
    distance_assignment = []
    type_assignment = []
    # use weighted random choice to assign the remainders for keeping the probability of original input
    for name, amount in distance_distribution.items():
        distance_assignment.extend([name] * amount )

    for name, amount in type_distribution.items():
        type_assignment.extend([name] * amount )
        if "unlimited_duration" in name:
            global UNLIMITED_FLOW
            UNLIMITED_FLOW += amount
    random.shuffle(distance_assignment)

    if type_to_parameter:
        type_assignment = [DEFAULT_FLOW_PARAMETERS[name] for name in type_assignment]
        return distance_assignment,type_assignment
    else:
        return distance_assignment,type_assignment,current_traffic_config.get('flow_parameters', DEFAULT_FLOW_PARAMETERS)

#TODO the config file is chaned, need to update the function
async def _handle_flow_command(net, args):
    global RUNNING, FILL_WIDTH, IPERF,FLOW_DIR

    RUNNING = 0
    # init APICOmmunicator if not yet done, or reset it for new experiment
    if type(INTERFACE) == APICommunicator:
        # Reset the communicator for reuse
        INTERFACE.reset_for_new_experiment()

    sim_time = 0

    name_to_parameter = {
        "duration(sec)": "-t",
        "size(bytes)": "-n",
        "rate(bits)": "-b",
    }

    if "--config" not in args:
        logger.warning("No config file")
        return

    try:
        config_file = args[args.index("--config") + 1]
    except (ValueError, IndexError):
        logger.warning("No config file path provided")
        return

    if not config_file.endswith(".json"):
        logger.warning("Configuration must be a JSON file")
        return

    logger.info(f"Loading configuration from {config_file}...")

    processed_intervals: List[Dict[str, Any]] = []
    
    with open(config_file, "r", encoding="utf-8") as f:
        try:
            config_data = json.load(f)

            logger.info("Checking configuration file...")
            try:
                processed_intervals, sim_time, IPERF = file_checker(config_data, "flow")
            except ValueError as exc:
                logger.warning(exc)
                IPERF = "iperf3"
                return
            logger.success("Configuration file is valid.")
            logger.success("Starting flow generation...")
        except json.JSONDecodeError as exc:
            logger.error(f"Error decoding JSON from {config_file}: {exc}")
            return

    FILL_WIDTH = len(str(sim_time))

    def get_current_config(current_time):
        if not processed_intervals:
            return None, None, 0

        temp_interval = processed_intervals[0]
        if current_time + 1 == temp_interval["end_time"]:
            del processed_intervals[0]

        return (
            temp_interval.get("varied_traffic"),
            temp_interval.get("fixed_traffic"),
            temp_interval["start_time"],
            temp_interval["duration_seconds"]
        )

    offset = 0.0
    start_t = time.perf_counter_ns()
    start_time = datetime.now().strftime("%Y-%m-%d_%H_%M_%S")
    if type(INTERFACE) == MininetCommunicator:
        FLOW_DIR = _create_exp_dir(DEFAULT_FLOW_DIR,start_time)

    logger.info("Start time: "+start_time)

    try:
        times = 0

        # start recycle ports task for APICommunicator
        if type(INTERFACE) == APICommunicator:
            asyncio.create_task(INTERFACE.recycle_port())

        while times < sim_time:
            step_time = time.perf_counter_ns()
            current_dynamic_traffic, current_fixed_traffic, interval_start_time,fixed_traffic_duration = get_current_config(times)

            connections = CONNECTIONS.copy()

            logger.info(f"Current simulation time pass {times} seconds")
            
            tasks = []

            if current_dynamic_traffic is not None:
                distance_assignment, type_assignment, current_parameters = probability_assignment(
                    current_dynamic_traffic, is_dynamic=True
                )

                connection_assignments = list(zip(distance_assignment, type_assignment))

                logger.debug(f"Connection assignments: {connection_assignments}")

                conn_dict = connections
                randrange = random.randrange
                
                async with LOCK:
                    RUNNING += len(connection_assignments)
                    logger.trace(f"RUNNING increased to {RUNNING}")

                src_list = []
                dst_list = []
                parameter_list = []

                for distance_name, type_name in connection_assignments:
                    
                    conns = conn_dict[distance_name]
                    connect = conns[randrange(len(conns))]

                    src = connect['src']['name']
                    dst = connect['dst']['name']
                    src_list.append(src)
                    dst_list.append(dst)
                    parameter = current_parameters[type_name]
                    parameter = {name_to_parameter[name]: parameter[name] for name in parameter.keys()}

                    if 'udp' in type_name:
                        parameter['-u'] = ' '
                    if 'unlimited_duration' in type_name:
                        parameter['-t'] = '0'

                    parameter_list.append(parameter)

                    logger.trace(f"Generating flow from {src} to {dst} with parameters {parameter}...")
                    
                tasks.append(
                    INTERFACE.start_iperf_pair(
                        net,
                        src_list.copy(),
                        dst_list.copy(),
                        parameter_list.copy(),
                        times,
                        iperf=IPERF,
                        flow_dir=FLOW_DIR,
                        fill_width=FILL_WIDTH,
                        cmd_lock=CMD_LOCK,
                        port_command=PORT_DISCOVERY_CMD,
                        start_time=(None if type(INTERFACE)==MininetCommunicator else start_time)
                    )
                )

            
            if interval_start_time == times:

                # start new fixed traffic if exists.
                if current_fixed_traffic is not None:
                    
                    distance_assignment, type_assignment, current_parameters = probability_assignment(
                        current_fixed_traffic, is_dynamic=False
                    )

                    logger.trace(f"Type assignment: {type_assignment}")
                    logger.trace(f"Distance assignment: {distance_assignment}")

                    connection_assignments = list(zip(distance_assignment, type_assignment))

                    logger.debug(f"Connection assignments: {connection_assignments}")

                    conn_dict = connections
                    randrange = random.randrange

                    async with LOCK:
                        RUNNING += len(connection_assignments)
                        logger.trace(f"RUNNING increased to {RUNNING}")

                    src_list = []
                    dst_list = []
                    parameter_list = []

                    for distance_name, type_name in connection_assignments:

                        conns = conn_dict[distance_name]
                        connect = conns[randrange(len(conns))]

                        src = connect['src']['name']
                        dst = connect['dst']['name']


                        src_list.append(src)
                        dst_list.append(dst)

                        parameter = current_parameters[type_name]
                        parameter = {name_to_parameter[name]: parameter[name] for name in parameter.keys()}

                        if 'udp' in type_name:
                            parameter['-u'] = ' '
                        if 'unlimited_duration' in type_name:
                            parameter['-t'] = '0'
                        if 'limited_duration' in type_name:
                            parameter['-t'] = parameter.get('-t',fixed_traffic_duration)

                        parameter_list.append(parameter)

                        logger.trace(f"Generating flow from {src} to {dst} with parameters {parameter}...")

                    tasks.append(
                        INTERFACE.start_fixed_iperf_pair(
                            net,
                            src_list.copy(),
                            dst_list.copy(),
                            parameter_list.copy(),
                            times,
                            fixed_traffic_duration,
                            iperf=IPERF,
                            flow_dir=FLOW_DIR,
                            fill_width=FILL_WIDTH,
                            cmd_lock=CMD_LOCK,
                            port_command=PORT_DISCOVERY_CMD,
                            start_time=(None if type(INTERFACE)==MininetCommunicator else start_time)
                        )
                    )            
            
            if tasks:
                # Properly await all tasks to ensure they complete before moving to next time step
                asyncio.gather(*tasks, return_exceptions=True)

            times += 1

            dur_time = (1.0 - ((time.perf_counter_ns() - step_time) / 1e9)) - offset

            if dur_time > 0:
                await asyncio.sleep(dur_time)

            total_time = (time.perf_counter_ns() - start_t) / 1e9
            logger.trace(f"{(total_time)} sec pass")
            offset = total_time - int(total_time)

        await ending_process(net)

    except KeyboardInterrupt:

        logger.warning("Experiment interrupted by user.")
        await ending_process(net)

#TODO : fix the logger.
async def _handle_dist_command(net, args):
    """
    Handle the 'dist' command to manage distribution of flows.
    """
    global RUNNING, FILL_WIDTH, IPERF

    RUNNING = 0

    # init APICOmmunicator if not yet done, or reset it for new experiment
    if type(INTERFACE) == APICommunicator:
        # Reset the communicator for reuse
        INTERFACE.reset_for_new_experiment()

    dist_data = None
    flow_size = None
    lifespan = None
    sending_rate = None

    flow_size_prob = None
    lifespan_prob = None
    sending_rate_prob = None

    if "--config" not in args:
        logger.warning("No config file")
        return

    # Load distribution json file
    if args and args[args.index("--config") + 1].endswith('.json'):
        dist_file = args[args.index("--config") + 1]
        dist_file_position = dist_file[:((dist_file.rfind('/') if dist_file.rfind('/') != -1 else dist_file.rfind('\\')) + 1)] # get prefix of dist_file
        logger.info(f"Loading distribution configuration from {dist_file}...")
        try :
            with open(dist_file, 'r') as f:
                # parse the value of csv file.
                dist_data = json.load(f)
                
                IPERF = dist_data.get('traffic_generator(iperf_or_iperf3)',IPERF)

                flow_size = dist_file_position+dist_data['flow_size_csv']
                lifespan = dist_file_position+dist_data['flow_duration_csv']
                sending_rate = dist_file_position+dist_data['flow_sending_rate_csv']

                try:
                    flow_size_df = pd.read_csv(flow_size)
                    lifespan_df = pd.read_csv(lifespan)
                    sending_rate_df = pd.read_csv(sending_rate)
                except Exception as e:
                    logger.error(f"Error reading CSV files: {e}")
                    return

                flow_size_df = flow_size_df[flow_size_df['probability'] != 0.0]
                lifespan_df = lifespan_df[lifespan_df['probability'] != 0.0]
                sending_rate_df = sending_rate_df[sending_rate_df['probability'] != 0.0]

                flow_size_prob = flow_size_df['probability'].to_numpy()
                lifespan_prob = lifespan_df['probability'].to_numpy()
                sending_rate_prob = sending_rate_df['probability'].to_numpy()

                flow_size = flow_size_df['bin_midpoint'].to_numpy()
                lifespan = lifespan_df['bin_midpoint'].to_numpy()
                sending_rate = sending_rate_df['bin_midpoint'].to_numpy()

                # get type and distance value.
                processed_intervals, sim_time, IPERF = file_checker(dist_data, "dist")

        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON from {dist_file}: {e}")
            return
    else:
        logger.warning("File formate is wrong...")
        return
    
    FILL_WIDTH = len(str(sim_time)) #Width for zero-padding in filenames

    def get_current_config(current_time):
        if not processed_intervals:
            return None, None, 0

        temp_interval = processed_intervals[0]
        if current_time + 1 == temp_interval["end_time"]:
            del processed_intervals[0]

        return (
            temp_interval.get("varied_traffic"),
            temp_interval.get("fixed_traffic"),
            temp_interval["start_time"],
            temp_interval["duration_seconds"]
        )
        
    times = 0
    offset = 0.0
    start_t = time.perf_counter_ns()
    start_time = datetime.now().strftime("%Y-%m-%d_%H_%M_%S")
    # await loop.run_in_executor(None, gdm.start)
    if type(INTERFACE) == MininetCommunicator:
        FLOW_DIR = _create_exp_dir(DEFAULT_FLOW_DIR,start_time)
    try:

        if type(INTERFACE) == APICommunicator:
            asyncio.create_task(INTERFACE.recycle_port())

        while times<sim_time:
        
            step_time = time.perf_counter_ns()

            current_dynamic_traffic, current_fixed_traffic, interval_start_time, _ = get_current_config(times)
            
            connections = CONNECTIONS.copy()

            logger.info(f"Current simulation time: {times} seconds")

            tasks = []

            # === dynamic traffic (varied_traffic) ===
            if current_dynamic_traffic is not None:

                distance_assignment,type_assignment = probability_assignment(
                    current_dynamic_traffic,
                    is_dynamic=True,
                    type_to_parameter=True
                )

                logger.debug(f"Type assignment: {type_assignment}")

                # Optimized block: bulk-sample choices and vectorize interval sampling
                da_len = len(distance_assignment)
                
                if da_len == 0:
                    pass
                else:
                    # Indices for each category
                    idx_t = [i for i, v in enumerate(type_assignment) if '-t' in v]
                    idx_n = [i for i, v in enumerate(type_assignment) if '-n' in v]
                    idx_b = [i for i, v in enumerate(type_assignment) if '-b' in v]
                    
                    # Vectorized sampling for each needed group
                    t_values = fast_random_choice(lifespan, lifespan_prob, len(idx_t)).astype(float) if idx_t else []
                    n_values = fast_random_choice(flow_size, flow_size_prob, len(idx_n)).astype(float) if idx_n else []
                    b_values = fast_random_choice(sending_rate, sending_rate_prob, len(idx_b)).astype(float) if idx_b else []



                    # Iterators (simple index counters) to pull from pre-sampled arrays
                    t_cursor = n_cursor = b_cursor = 0

                    # Local bindings for speed
                    conn_dict = connections
                    randrange = random.randrange

                    async with LOCK:
                        RUNNING += da_len

                    src_list = []
                    dst_list = []
                    parameter_list = []

                    for i, distance_name in enumerate(distance_assignment):
                        conns = conn_dict[distance_name]

                        connect = conns[randrange(len(conns))]
                        src = connect['src']['name']
                        dst = connect['dst']['name']

                        parameters = {}

                        if '-t' in type_assignment[i]:
                            parameters['-t'] = float(t_values[t_cursor])
                            if parameters['-t'] <= 1.0:
                                parameters['-t'] = 1.0  # Ensure minimum duration of 1 second
                            elif parameters['-t'] > 5.0:
                                parameters['-t'] = 5.0  # Cap maximum duration to 5 seconds for fixed flows
                            t_cursor += 1

                        if '-n' in type_assignment[i]:
                            parameters['-n'] = f"{n_values[n_cursor]}M"
                            n_cursor += 1

                        if '-b' in type_assignment[i]:
                            parameters['-b'] = f"{b_values[b_cursor]}M"
                            b_cursor += 1

                        if '-u' in type_assignment[i]:
                            parameters['-u'] = ' '
                        
                        if '-un' in type_assignment[i]:
                            parameters['-t'] = '0'

                        logger.debug(f"Generating flow from {src} to {dst} with parameters {parameters}...")

                        src_list.append(src)
                        dst_list.append(dst)
                        parameter_list.append(parameters)

                    tasks.append(
                        INTERFACE.start_iperf_pair(
                            net,
                            src_list,
                            dst_list,
                            parameter_list,
                            times,
                            iperf=IPERF,
                            flow_dir=FLOW_DIR,
                            fill_width=FILL_WIDTH,
                            cmd_lock=CMD_LOCK,
                            port_command=PORT_DISCOVERY_CMD,
                            start_time=(None if type(INTERFACE)==MininetCommunicator else start_time)
                        )
                    )

            # === fixed traffic (fixed_traffic) ===
            if interval_start_time == times and current_fixed_traffic is not None:

                distance_assignment,type_assignment = probability_assignment(
                    current_fixed_traffic,
                    is_dynamic=False,
                    type_to_parameter=True
                )

                logger.debug(f"Fixed type assignment: {type_assignment}")

                da_len_fixed = len(distance_assignment)
                if da_len_fixed == 0:
                    pass
                else:
                    idx_t = [i for i, v in enumerate(type_assignment) if '-t' in v]
                    idx_n = [i for i, v in enumerate(type_assignment) if '-n' in v]
                    idx_b = [i for i, v in enumerate(type_assignment) if '-b' in v]

                    t_values = fast_random_choice(lifespan, lifespan_prob, len(idx_t)).astype(float) if idx_t else []
                    n_values = fast_random_choice(flow_size, flow_size_prob, len(idx_n)).astype(float) if idx_n else []
                    b_values = fast_random_choice(sending_rate, sending_rate_prob, len(idx_b)).astype(float) if idx_b else []

                    t_cursor = n_cursor = b_cursor = 0
                    conn_dict = connections
                    randrange = random.randrange

                    async with LOCK:
                        RUNNING += da_len_fixed

                    src_list = []
                    dst_list = []
                    parameter_list = []

                    for i, distance_name in enumerate(distance_assignment):
                        conns = conn_dict[distance_name]

                        connect = conns[randrange(len(conns))]
                        src = connect['src']['name']
                        dst = connect['dst']['name']

                        parameters = {}

                        if '-t' in type_assignment[i]:
                            parameters['-t'] = float(t_values[t_cursor])
                            if parameters['-t'] <= 1.0:
                                parameters['-t'] = 1.0  # Ensure minimum duration of 1 second
                            elif parameters['-t'] > 5.0:
                                parameters['-t'] = 5.0  # Cap maximum duration to 5 seconds for fixed flows
                            t_cursor += 1

                        if '-n' in type_assignment[i]:
                            parameters['-n'] = f"{n_values[n_cursor]}M"
                            n_cursor += 1

                        if '-b' in type_assignment[i]:
                            parameters['-b'] = f"{b_values[b_cursor]}M"
                            b_cursor += 1

                        if '-u' in type_assignment[i]:
                            parameters['-u'] = ' '
                        
                        if '-un' in type_assignment[i]:
                            parameters['-t'] = '0'

                        logger.trace(f"Generating fixed flow from {src} to {dst} with parameters {parameters}...")

                        src_list.append(src)
                        dst_list.append(dst)
                        parameter_list.append(parameters)

                    tasks.append(
                        INTERFACE.start_iperf_pair(
                            net,
                            src_list,
                            dst_list,
                            parameter_list,
                            times,
                            iperf=IPERF,
                            flow_dir=FLOW_DIR,
                            fill_width=FILL_WIDTH,
                            cmd_lock=CMD_LOCK,
                            port_command=PORT_DISCOVERY_CMD,
                            start_time=(None if type(INTERFACE)==MininetCommunicator else start_time)
                        )
                    )

            if tasks:
                asyncio.gather(*tasks, return_exceptions=True)


            times += 1
            dur_time = (1.0-((time.perf_counter_ns()-step_time)/1e9)) - offset

            if dur_time > 0:
                await asyncio.sleep(dur_time)  # Wait for some second before the next iteration
            total_time = (time.perf_counter_ns()-start_t)/1e9
            logger.info(f"{(total_time)} sec pass")
            offset = total_time - int(total_time)

        await ending_process(net)

    except KeyboardInterrupt:
        logger.warning("Experiment interrupted by user.")
        await _handle_exit_command("Experiment", leave=True)

async def _handle_exit_command(type="CLI", show_msg: bool = True, leave: Optional[bool] = False):

    if show_msg:
        logger.warning(f"Exiting {type} and cleaning up...")

    #TODO rewrite below code in MininetCommunicator and APICommunicator
    #TODO change below code.
    loop = asyncio.get_running_loop()

    await INTERFACE.cleanup(loop, leave=leave)

    if leave:
        NTG_CONFIG.run(task=execute_command,mode=2)
    # await loop.run_in_executor(None, gdm.terminate)

if __name__ == "__main__":
    interactive_command_mode(None,"NTG.yaml")
