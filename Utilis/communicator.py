"""
NTG Utilis - Communicator Module

This module defines the Communicator abstract base class and its implementations for Mininet and API-based communication.
"""
import asyncio
from abc import ABC, abstractmethod
from typing import Awaitable, Callable, Dict, List, Optional, Tuple,Any
from pydantic import BaseModel, Field
from requests.adapters import HTTPAdapter
import requests
import os
import time
import random
from loguru import logger

HTTP_CONN_LIMIT = int(os.getenv("HTTP_CONN_LIMIT", "1024"))
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "5.0"))

class Communicator(ABC):

    def __init__(self,Ctype):
        self.Ctype = Ctype

    def show(self):
        print(self.Ctype)

    @abstractmethod
    def start_iperf_pair(self, *args, **kwargs) -> None:
        ...
    
    @abstractmethod
    def start_fixed_iperf_pair(self, *args, **kwargs) -> None:
        ...
    
    @abstractmethod
    def cleanup(self,loop:asyncio.AbstractEventLoop) -> None:
        ...

class SenderReq(BaseModel):
    c: str
    port: int
    u: bool = False
    B: Optional[str] = None
    cport: Optional[int]
    b: Optional[str] = None
    n: Optional[str] = None
    t: Optional[int] = None
    start_time: Optional[str] = None
    fixed_traffic_duration: Optional[int] = None
    iperf_version: Optional[str] = "iperf3"

class ReceiverReq(BaseModel):
    bind: Optional[str] = None
    port: int
    u: bool = False
    one_off: bool = True
    start_time: Optional[str] = None
    fixed_traffic_duration: Optional[int] = None
    wait_offset: Optional[float] = 0.0
    iperf_version: Optional[str] = "iperf3"

class SenderReqs(BaseModel):
    reqs: List[SenderReq] = Field(default_factory=list)

class ReceiverReqs(BaseModel):
    reqs: List[ReceiverReq] = Field(default_factory=list)


class MininetCommunicator(Communicator):
    def __init__(self,sleep_time: Dict[str,float],logger,on_flow_finished: Optional[Callable[[int], Awaitable[None]]] = None):
        super().__init__("Mininet")
        self.logger = logger
        self.on_flow_finished = on_flow_finished
        self.sleep_time = self.sleep_time = (sleep_time.get("min",0.5),sleep_time.get("max",1.5))

    async def host_cmd(self, host, command: str) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, host.cmd, command)

    async def host_popen(self, host, argv: List[str]):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, host.popen, argv)

    async def process_wait(self, process) -> Tuple[Optional[bytes], Optional[bytes]]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, process.communicate)

    async def process_terminate(self, process) -> None:
        loop = asyncio.get_running_loop()

        def _terminate() -> None:
            try:
                process.terminate()
            except Exception:
                pass
            try:
                process.wait(timeout=1)
            except Exception:
                pass

        await loop.run_in_executor(None, _terminate)

    async def _start_single_iperf_pair(
        self,
        net,
        src: str,
        dst: str,
        parameter: Dict[str, str],
        times: int,
        port: int,
        *,
        iperf: str,
        flow_dir: str,
        fill_width: int,
        start_time: Optional[str] = None,
    ) -> None:
        """Internal method to start a single iperf pair with a pre-assigned port."""
        try:
            dst_host = net.get(dst)

            self.logger.trace(f"Starting {iperf} server on {dst}:{port}...")
            out_file_s = ""
            out_file_c = ""

            if iperf == "iperf3":
                out_file_s = f"-J --logfile {flow_dir}/receiver_{dst.replace('.','_')}_{port}_{'udp' if parameter.get('u') else 'tcp'}_{int(time.time())}.json"
                out_file_c = f"-J --logfile {flow_dir}/sender_{dst.replace('.','_')}_{port}_{'udp' if parameter.get('u') else 'tcp'}_{int(time.time())}.json"
            elif iperf == "iperf":
                out_file_s = f"-e -o {flow_dir}/receiver_{dst.replace('.','_')}_{port}_{'udp' if parameter.get('u') else 'tcp'}_{int(time.time())}.txt -i 1"
                out_file_c = f"-e -o {flow_dir}/sender_{dst.replace('.','_')}_{port}_{'udp' if parameter.get('u') else 'tcp'}_{int(time.time())}.txt -i 1"
            cmd_server = f"{iperf} -s -1 -p {port} {out_file_s}"
            server_proc = await self.host_popen(dst_host, cmd_server.split())

            await asyncio.sleep(random.uniform(*self.sleep_time))

            src_host = net.get(src)
            cmd_client = f"{iperf} -c {dst_host.IP()} "
            param_str = " ".join([f"{k} {v}" for k, v in parameter.items()])

            if param_str.startswith('-n') and '-b' not in parameter:
                param_str += ' -b 1G'

            cmd_client += f" {param_str} -l 0.125K -p {port} {out_file_c} "

            client_proc = await self.host_popen(src_host, cmd_client.split())
            self.logger.trace(f"Started client process {client_proc.pid} on {src} -> {dst}:{port}")

            _stdout, stderr = await self.process_wait(client_proc)
            exit_code = client_proc.returncode

            if exit_code > 1 and stderr:
                self.logger.warning(
                    f"Client {src} -> {dst}:{port} exited with {exit_code}: {stderr.decode(errors='ignore').strip()}"
                )

            await self.process_wait(server_proc)

            if self.on_flow_finished:
                await self.on_flow_finished(1)

            self.logger.debug(f"Completed {iperf} between {src} and {dst} on port {port}")

        except Exception as exc:
            self.logger.warning(f"Error starting {iperf} between {src} and {dst}: {exc}")

    async def start_iperf_pair(
        self,
        net,
        src_list: List[str],
        dst_list: List[str],
        parameter_list: List[Dict[str, str]],
        times: int,
        *,
        iperf: str,
        flow_dir: str,
        fill_width: int,
        cmd_lock: asyncio.Lock,
        port_command: str,
        start_time: Optional[str] = None,
    ) -> None:
        """
        Start multiple iperf pairs concurrently.
        
        Args:
            net: Mininet network object 
            src_list: List of source host names
            dst_list: List of destination host names
            parameter_list: List of parameter dictionaries for each flow
            times: Flow index for logging
            iperf: iperf version to use ("iperf" or "iperf3")
            flow_dir: Directory to save flow logs
            fill_width: Zero-padding width for log filenames
            cmd_lock: Lock for port allocation commands
            port_command: Command to get available port
            start_time: Optional start time string
        """
        try:
            # Allocate ports for all destinations
            port_list = []
            for dst in dst_list:
                dst_host = net.get(dst)
                async with cmd_lock:
                    port_output = await self.host_cmd(dst_host, port_command)
                port = int(port_output.strip())
                port_list.append(port)

            # Start all iperf pairs concurrently
            tasks = []
            for src, dst, parameter, port in zip(src_list, dst_list, parameter_list, port_list):
                task = self._start_single_iperf_pair(
                    net,
                    src,
                    dst,
                    parameter,
                    times,
                    port,
                    iperf=iperf,
                    flow_dir=flow_dir,
                    fill_width=fill_width,
                    start_time=start_time,
                )
                tasks.append(task)

            await asyncio.gather(*tasks, return_exceptions=True)

        except Exception as exc:
            self.logger.warning(f"Error starting {iperf} pairs: {exc}")

    async def _start_single_fixed_iperf_pair(
        self,
        net,
        src: str,
        dst: str,
        parameter: Dict[str, str],
        times: int,
        duration: int,
        port: int,
        *,
        iperf: str,
        flow_dir: str,
        fill_width: int,
        start_time: Optional[str] = None,
    ) -> None:
        """Internal method to start a single fixed-duration iperf pair with a pre-assigned port."""
        try:
            dst_host = net.get(dst)

            self.logger.debug(f"Starting fixed {iperf} server on {dst}:{port}...")
            out_file_s = ""
            out_file_c = ""

            if iperf == "iperf3":
                out_file_s = f"-J --logfile {flow_dir}/receiver_{dst.replace('.','_')}_{port}_{'udp' if parameter.get('u') else 'tcp'}_{int(time.time())}.json"
                out_file_c = f"-J --logfile {flow_dir}/sender_{dst.replace('.','_')}_{port}_{'udp' if parameter.get('u') else 'tcp'}_{int(time.time())}.json"
            elif iperf == "iperf":
                out_file_s = f"-e -o {flow_dir}/receiver_{dst.replace('.','_')}_{port}_{'udp' if parameter.get('u') else 'tcp'}_{int(time.time())}.txt -i 1"
                out_file_c = f"-e -o {flow_dir}/sender_{dst.replace('.','_')}_{port}_{'udp' if parameter.get('u') else 'tcp'}_{int(time.time())}.txt -i 1"
            cmd_server = f"{iperf} -s -p {port} {out_file_s}"
            server_proc = await self.host_popen(dst_host, cmd_server.split())

            await asyncio.sleep(random.uniform(*self.sleep_time))

            src_host = net.get(src)
            cmd_client = f"{iperf} -c {dst_host.IP()} "
            param_str = " ".join([f"{k} {v}" for k, v in parameter.items()])

            if param_str.startswith('-n') and '-b' not in parameter:
                param_str += ' -b 1G'

            cmd_client += f" {param_str} -l 0.125K -p {port} {out_file_c} "

            cur_time = time.perf_counter_ns()
            while time.perf_counter_ns() - cur_time < duration * (10**9):
                client_proc = await self.host_popen(src_host, cmd_client.split())
                await self.process_wait(client_proc)

            await self.process_terminate(server_proc)

            if self.on_flow_finished:
                await self.on_flow_finished(1)
            self.logger.debug(f"Completed {iperf} between {src} and {dst} on port {port}")
            
        except Exception as exc:
            self.logger.warning(f"Error starting fixed {iperf} between {src} and {dst}: {exc}")

    async def start_fixed_iperf_pair(
        self,
        net,
        src_list: List[str],
        dst_list: List[str],
        parameter_list: List[Dict[str, str]],
        times: int,
        fixed_traffic_duration: int,
        *,
        iperf: str,
        flow_dir: str,
        fill_width: int,
        cmd_lock: asyncio.Lock,
        port_command: str,
        start_time: Optional[str] = None,
    ) -> None:
        """
        Start multiple fixed-duration iperf pairs concurrently.
        
        Args:
            net: Mininet network object
            src_list: List of source host names
            dst_list: List of destination host names
            parameter_list: List of parameter dictionaries for each flow
            times: Flow index for logging
            fixed_traffic_duration: Duration in seconds for fixed traffic
            iperf: iperf version to use ("iperf" or "iperf3")
            flow_dir: Directory to save flow logs
            fill_width: Zero-padding width for log filenames
            cmd_lock: Lock for port allocation commands
            port_command: Command to get available port
            start_time: Optional start time string
        """
        try:
            # Allocate ports for all destinations
            port_list = []
            for dst in dst_list:
                dst_host = net.get(dst)
                async with cmd_lock:
                    port_output = await self.host_cmd(dst_host, port_command)
                port = int(port_output.strip())
                port_list.append(port)

            # Start all fixed iperf pairs concurrently
            tasks = []
            for src, dst, parameter, port in zip(src_list, dst_list, parameter_list, port_list):
                task = self._start_single_fixed_iperf_pair(
                    net,
                    src,
                    dst,
                    parameter,
                    times,
                    fixed_traffic_duration,
                    port,
                    iperf=iperf,
                    flow_dir=flow_dir,
                    fill_width=fill_width,
                    start_time=start_time,
                )
                tasks.append(task)

            await asyncio.gather(*tasks, return_exceptions=True)

        except Exception as exc:
            self.logger.warning(f"Error starting fixed {iperf} pairs: {exc}")

    async def cleanup(self,loop:asyncio.AbstractEventLoop,leave: Optional[bool] = False):
        await loop.run_in_executor(None, os.system, "sudo killall -s SIGTERM iperf3")
        await loop.run_in_executor(None, os.system, "sudo killall -s SIGTERM iperf")


class HostClient:
    def __init__(self,logger):
        self.session = requests.Session()
        adapter = HTTPAdapter(
            pool_connections=HTTP_CONN_LIMIT,
            pool_maxsize=HTTP_CONN_LIMIT,
            max_retries=0,
        )
        self.session.mount("http://", adapter)
        self.timeout = HTTP_TIMEOUT
        self.logger = logger

    async def _post_json(
        self, url: str, body: Dict[str, Any]
    ) -> Optional[requests.Response]:
        loop = asyncio.get_running_loop()

        def _do():
            return self.session.post(url, json=body, timeout=self.timeout)

        try:
            return await loop.run_in_executor(None, _do)
        except Exception as e:
            self.logger.warning(f"POST {url} err: {e}")
            return None
        
    async def _get_json(
        self, url: str
    )-> Optional[requests.Response]:
        loop = asyncio.get_running_loop()

        def _do():
            return self.session.get(url, timeout=self.timeout)

        try:
            return await loop.run_in_executor(None, _do)
        except Exception as e:
            self.logger.warning(f"GET {url} err: {e}")
            return None

    async def start_receiver(
        self, 
        api: str, 
        req: ReceiverReqs
    ) -> Tuple[Optional[str], Optional[int]]:
        body = req.dict()
        r = await self._post_json(f"{api}/flow/receiver", body)
        if r is None:
            return None, None
        if r.status_code == 200:
            try:
                return r.json(), 200
            except Exception:
                self.logger.warning(f"receiver {body} malformed JSON")
                return None, 200
        self.logger.warning(f"receiver {body} http={r.status_code} {r.text}")
        return None, r.status_code

    async def start_sender(
        self, api: str, req: SenderReqs
    ) -> Tuple[Optional[str], Optional[int]]:
        body = req.dict()
        r = await self._post_json(f"{api}/flow/sender", body)
        if r is None:
            return None, None
        if r.status_code == 200:
            try:
                return r, 200
            except Exception:
                self.logger.warning(f"sender {body} malformed JSON")
                return None, 200
        self.logger.warning(f"sender {body} http={r.status_code} {r.text}")
        return None, r.status_code

    async def get_finished_ports(self, api: str) -> Dict[str,List[int]]:
        r = await self._get_json(f"{api}/flow/finished_ports")
        if r is None:
            return None
        if r.status_code == 200:
            try:
                return r.json()
            except Exception:
                self.logger.warning(f"get_finished_ports malformed JSON")
                return {}
        self.logger.warning(f"get_finished_ports http={r.status_code} {r.text}")
        return {}
    
    async def stop_flow(self, api: str, job_id: str) -> bool:
        r = await self._post_json(f"{api}/jobs/{job_id}/stop", {})
        if r is None:
            return False
        if r.status_code == 200:
            return True
        self.logger.warning(f"stop_flow {job_id} http={r.status_code} {r.text}")
        return False

    async def stop_all_flow(self, api: str) -> bool:
        r = await self._post_json(f"{api}/jobs/stop_all", {})
        if r is None:
            return False
        if r.status_code == 200 :
            return r.json()
        self.logger.warning(f"stop_all_flow http={r.status_code} {r.text}")
        return False

    async def close(self):
        self.session.close()

class APICommunicator(Communicator):
    
    API = list()

    HOSTS_NAME_MAP: Dict[str, List[str]] = dict()

    def __init__(self,sleep_time: Dict[str,float],logger,on_flow_finished: Optional[Callable[[int], Awaitable[None]]] = None,
                 recycle_stop_event: Optional[asyncio.Event] = None, worker_node_server: Optional[Dict[str, Any]] = None,
                 recycle_interval:Optional[int] = 10,
                 ports_limitation: Optional[Dict[str, List[int]]] = {"exclude_ports":[8000]}):
        
        super().__init__("API")
        self.logger = logger
        self.logger.debug(f"Setting : sleep_time={sleep_time}, recycle_interval={recycle_interval}, ports_limitation={ports_limitation}")
        self.on_flow_finished = on_flow_finished
        self.recycle_stop_event = recycle_stop_event
        self.recycle_port_ended = asyncio.Event()
        
        self.API = worker_node_server.get("api_servers",self.API)
        self.HOSTS_NAME_MAP = worker_node_server.get("hosts_name_map",self.HOSTS_NAME_MAP)

        self.recycle_interval = recycle_interval

        self.sleep_time = (sleep_time.get("min",0.5),sleep_time.get("max",1.5))

        self.available_ports = {
            host: asyncio.Queue() for host in self.HOSTS_NAME_MAP.keys()
        }

        exclude_ports = ports_limitation.get("exclude_ports", [8000])
        self.max_port = ports_limitation.get("max_port", 16205)
        self.min_port = ports_limitation.get("min_port", 5204)
        self.ports_len = None
        if (self.max_port - self.min_port <= len(exclude_ports) ) or (self.max_port <= self.min_port):
            raise ValueError("Invalid port range or too many excluded ports.")

        for host in self.available_ports:

            for port in range(self.min_port, self.max_port):
                if port in exclude_ports:
                    continue
                self.available_ports[host].put_nowait(port)

            if self.ports_len is None:
                self.ports_len = self.available_ports[host].qsize()

        self.client = HostClient(logger)

    def reset_for_new_experiment(self):
        """Reset the APICommunicator state for a new experiment while reusing the same instance."""
        self.logger.info("Resetting APICommunicator for new experiment...")
        
        # Reset events
        self.recycle_stop_event.clear()
        self.recycle_port_ended.clear()

    async def check_restore_ports(self)->bool:
        for host in self.available_ports:
            if self.available_ports[host].qsize() < self.ports_len:
                self.logger.info(f"Ports on {host} not fully restored yet: {self.available_ports[host].qsize()}/{self.ports_len}")
                return False
        return True

    async def recycle_port(self) -> None:
        self.logger.info("Starting recycle_port task...")
        try:
            while not self.recycle_stop_event.is_set():
                await asyncio.sleep(self.recycle_interval)
                port_cnt = 0
                for api in self.API:
                    self.logger.debug(f"Checking finished ports from {api}...")
                    finished_ports = await self.client.get_finished_ports(api)

                    # if API server is down or has error
                    if finished_ports is None:
                        continue

                    port_cnt += sum(len(ports) for ports in finished_ports.values())
                    for host, ports in finished_ports.items():
                        for port in ports:
                            self.available_ports[host].put_nowait(port)
                        self.logger.trace(f"[{api}] {host} Recycled ports: {ports}")
                self.logger.info(f"Recycled {port_cnt} ports")
                await self.on_flow_finished(port_cnt)

            while not await self.check_restore_ports():
                await asyncio.sleep(1)
                port_cnt = 0
                for api in self.API:
                    finished_ports = await self.client.get_finished_ports(api)

                    # if API server is down or has error
                    if finished_ports is None:
                        continue

                    port_cnt += sum(len(ports) for ports in finished_ports.values())
                    for host, ports in finished_ports.items():
                        for port in ports:
                            self.available_ports[host].put_nowait(port)
                        self.logger.trace(f"[{api}] {host} Recycled ports: {ports}")
                self.logger.info(f"Recycled {port_cnt} ports during cleanup")
                await self.on_flow_finished(port_cnt)

            # Mark recycle port task as completed
            self.logger.success("Recycle port task ended, all ports restored.")
            self.recycle_port_ended.set()

        except Exception as e:
            self.logger.warning(f"Error in recycle_port: {e}")


    async def port_selector(self,dst:str)->int:
        port = await self.available_ports[dst].get()
        return port

    async def start_iperf_pair(
        self,
        net,
        src_list: List[str],
        dst_list: List[str],
        parameter_list: List[Dict[str, str]],
        times: int,
        *,
        iperf: str,
        flow_dir: str,
        fill_width: int,
        cmd_lock: Any,
        port_command: Any,
        start_time: Optional[str] = None,
    ) -> None:
        try:
            
            port_list = []

            rreqs = {}

            for _, dst in enumerate(dst_list):

                port = await self.port_selector(dst)
                port_list.append(port)

                self.logger.trace(f"Starting {iperf} server on {dst}:{port}...")
                
                rreq = ReceiverReq(
                    bind=self.HOSTS_NAME_MAP[dst]['ip'],
                    port=port,
                    u=False,
                    one_off=True,
                    start_time=start_time,
                    iperf_version=iperf,
                )
                rreqs.setdefault(self.HOSTS_NAME_MAP[dst]["api"],ReceiverReqs())

                rreqs[self.HOSTS_NAME_MAP[dst]["api"]].reqs.append(rreq)

            api_rjobs = {}
            for api, rreq in rreqs.items():

                rjobs, status = await self.client.start_receiver(
                    api, 
                    rreq
                )

                api_rjobs[api] = rjobs

                if not rjobs:
                    self.logger.warning(f"Failed to start receiver on {api}, status: {status}")
                    for dst, port in zip(dst_list, port_list):
                        self.available_ports[dst].put_nowait(port)
                    await self.on_flow_finished(len(dst_list))
                    return
                
            await asyncio.sleep(random.uniform(*self.sleep_time))

            sreq = {}
            
            for src,dst,port,parameter in zip(src_list,dst_list,port_list,parameter_list):

                udp = parameter.get('-u', False)
                if udp == ' ':
                    udp = True

                req = SenderReq(
                    c=self.HOSTS_NAME_MAP[dst]['ip'],
                    port=port,
                    u=udp,
                    B=self.HOSTS_NAME_MAP[src]['ip'],
                    cport=None,
                    b=parameter.get('-b',None),
                    n=parameter.get('-n'),
                    t= (int(parameter.get('-t')) if parameter.get('-t') is not None else None),
                    start_time=start_time,
                    iperf_version=iperf,
                )

                sreq.setdefault(self.HOSTS_NAME_MAP[src]["api"], SenderReqs())
                sreq[self.HOSTS_NAME_MAP[src]["api"]].reqs.append(req)

            for src, req in sreq.items():
                sjob, status = await self.client.start_sender(
                    src, req
                )

                if not sjob:
                    self.logger.warning(f"Failed to start sender on {src} -> {dst}:{port}, status: {status}")
                    # Stop all started receivers
                    for api, rjobs in api_rjobs.items():
                        for rjob in rjobs:
                            self.client.stop_flow(api, rjob)
                        
                    # Recycle ports
                    for dst, port in zip(dst_list, port_list):
                        self.available_ports[dst].put_nowait(port)

                    await self.on_flow_finished(len(src_list)+len(dst_list))
                    return

        except Exception as exc:
            self.logger.warning(f"Error starting {iperf} between {src} and {dst}: {exc}")

    async def start_fixed_iperf_pair(
        self,
        net,
        src_list: List[str],
        dst_list: List[str],
        parameter_list: List[Dict[str, str]],
        times: int,
        fixed_traffic_duration: int,
        *,
        iperf: str,
        flow_dir: str,
        fill_width: int,
        cmd_lock: Any,
        port_command: Any,
        start_time: Optional[str] = None,
        iperf_version: Optional[str] = "iperf3",
    ) -> None:
        try:
            self.logger.trace(f"Starting fixed {iperf} between multiple pairs...")
            wait_offset = random.uniform(*self.sleep_time)
            port_list = []
            rreqs = {}
            logger.trace(dst_list)
            logger.trace(parameter_list)
            for dst, parameter in zip(dst_list, parameter_list):
                port = await self.port_selector(dst)
                self.logger.trace(f"Starting fixed {iperf} server on {dst}:{port}")
                port_list.append(port)

                self.logger.trace(f"Starting fixed {iperf} server on {dst}:{port}...")

                t_para = parameter.get('-t',-1)

                is_unlimited_duration = False if (t_para != 0 ) else True
                
                rreq = ReceiverReq(
                    bind=self.HOSTS_NAME_MAP[dst]['ip'],
                    port=port,
                    u=False,
                    one_off=False if t_para == -1 else True,
                    start_time=start_time,
                    fixed_traffic_duration= None if is_unlimited_duration else fixed_traffic_duration,
                    wait_offset=(wait_offset+10.0), # add extra wait time for ensure receiver started before sender
                    iperf_version=iperf,
                )
                rreqs.setdefault(self.HOSTS_NAME_MAP[dst]["api"],ReceiverReqs())
                rreqs[self.HOSTS_NAME_MAP[dst]["api"]].reqs.append(rreq)

            api_rjobs = {}
            for api, rreq in rreqs.items():

                rjobs, status = await self.client.start_receiver(
                    api, 
                    rreq
                )
                api_rjobs[api] = rjobs

                if not rjobs:
                    self.logger.warning(f"Failed to start receiver on {api}, status: {status}")
                    for dst, port in zip(dst_list, port_list):
                        self.available_ports[dst].put_nowait(port)
                    await self.on_flow_finished(len(dst_list))
                    return
                
            await asyncio.sleep(wait_offset)

            sreq = {}

            for src,dst,port,parameter in zip(src_list,dst_list,port_list,parameter_list):

                udp = parameter.get('-u', False)
                if udp == ' ':
                    udp = True

                req = SenderReq(
                    c=self.HOSTS_NAME_MAP[dst]['ip'],
                    port=port,
                    u=udp,
                    B=self.HOSTS_NAME_MAP[src]['ip'],
                    cport=None,
                    b=parameter.get('-b',None),
                    n=parameter.get('-n'),
                    t= (int(parameter.get('-t')) if parameter.get('-t') is not None else None),
                    start_time=start_time,
                    fixed_traffic_duration= None if is_unlimited_duration else fixed_traffic_duration,
                    iperf_version=iperf,
                )
                sreq.setdefault(self.HOSTS_NAME_MAP[src]["api"], SenderReqs())
                sreq[self.HOSTS_NAME_MAP[src]["api"]].reqs.append(req)
                
            for src, req in sreq.items():
                sjob, status = await self.client.start_sender(
                    src, req
                )
                if not sjob:
                    self.logger.warning(f"Failed to start fixed sender on {src} -> {dst}:{port}, status: {status}")
                    # Stop all started receivers
                    for api, rjobs in api_rjobs.items():
                        for rjob in rjobs:
                            self.client.stop_flow(api, rjob)
                        
                    # Recycle ports
                    for dst, port in zip(dst_list, port_list):
                        self.available_ports[dst].put_nowait(port)

                    await self.on_flow_finished(len(src_list)+len(dst_list))
                    return
                
        except Exception as exc:
            self.logger.warning(f"Error starting fixed {iperf} between {src} and {dst}: {exc}")
    
    async def cleanup(self,loop:asyncio.AbstractEventLoop, leave: Optional[bool] = False):
        for api in self.API:
            val = await self.client.stop_all_flow(api)
            self.logger.info(f"Stopped flows on {api}")

        self.recycle_stop_event.set()

        if not leave: # if leaving, do not need to wait for recycling
            await self.recycle_port_ended.wait()

if __name__ == "__main__":
    pass