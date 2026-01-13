# Network Traffic Generator — Development Manual

## Table of Contents

1. [Introduction](#introduction)
2. [System Architecture Overview](#system-architecture-overview)
3. [Core Modules](#core-modules)
   - [interactive_commands.py](#interactive_commandspy)
   - [Utilis Package](#utilis-package)
4. [Key Design Patterns](#key-design-patterns)
5. [Developing New Features](#developing-new-features)
   - [Adding New Commands](#adding-new-commands)
   - [Creating New Communicators](#creating-new-communicators)
   - [Extending Utility Functions](#extending-utility-functions)
6. [API Reference](#api-reference)
7. [Best Practices](#best-practices)
8. [Troubleshooting](#troubleshooting)

---

## Introduction

NTG (Network Traffic Generator) is a Python-based system designed for generating and managing network traffic in both Mininet emulated environments and hardware testbeds. This manual is intended for developers who want to extend or customize the NTG system.

### Prerequisites

- Python 3.8+
- Understanding of asyncio programming
- Familiarity with network concepts (iperf, TCP/UDP protocols)
- Basic knowledge of Mininet (for Mininet mode development)

### Dependencies

```python
# Core dependencies
asyncio          # Asynchronous I/O
loguru           # Logging framework
pandas           # Data manipulation
numpy            # Numerical operations
requests         # HTTP client
pydantic         # Data validation
paramiko         # SSH communication
nornir           # Automation framework
prompt_toolkit   # Interactive CLI
```

---

## System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    interactive_commands.py                      │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │   CLI Loop  │  │  Commands   │  │   Traffic Generation    │  │
│  │  (asyncio)  │  │  Handlers   │  │      Engine             │  │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Utilis Package                              │
│  ┌─────────────────┐  ┌─────────────────┐  ┌────────────────┐   │
│  │ communicator.py │  │ command_utils.py│  │distance_       │   │
│  │  - Communicator │  │  - File utils   │  │seperate.py     │   │
│  │  - MininetComm  │  │  - Validation   │  │  - Topology    │   │
│  │  - APIComm      │  │  - SSH helpers  │  │    analysis    │   │
│  └─────────────────┘  └─────────────────┘  └────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              │
                ┌─────────────┴─────────────┐
                ▼                           ▼
        ┌───────────────┐           ┌───────────────┐
        │    Mininet    │           │   Hardware    │
        │   Testbed     │           │   Testbed     │
        └───────────────┘           └───────────────┘
```

---

## Core Modules

### interactive_commands.py

This is the main entry point of the NTG system. It provides:

1. **Interactive CLI** - An async command-line interface using `prompt_toolkit`
2. **Traffic Generation Engine** - Manages flow generation based on configuration
3. **Command Handlers** - Processes user commands (`flow`, `dist`, `exit`)

#### Key Global Variables

| Variable | Type | Description |
|----------|------|-------------|
| `INTERFACE` | `Communicator` | Current communication interface (Mininet or API) |
| `NTG_CONFIG` | `Nornir` | Configuration loaded from NTG.yaml |
| `CONNECTIONS` | `Dict` | Host pair connections categorized by distance |
| `HOSTS` | `Dict` | Map of host names and their properties |
| `RUNNING` | `int` | Count of currently active flows |
| `LOCK` | `asyncio.Lock` | Thread-safe lock for shared state |

#### Entry Point Function

```python
def interactive_command_mode(net, config_file_path: Optional[str] = "NTG.yaml"):
    """
    Main entry point for NTG interactive mode.
    
    Args:
        net: Mininet network instance (None for hardware mode)
        config_file_path: Path to NTG configuration file
    """
```

---

### Utilis Package

#### communicator.py

Contains the communication abstraction layer with three main classes:

##### 1. `Communicator` (Abstract Base Class)

```python
class Communicator(ABC):
    """
    Abstract base class for all communicators.
    Defines the interface that all communicators must implement.
    """
    
    @abstractmethod
    def start_iperf_pair(self, *args, **kwargs) -> None:
        """Start a dynamic iperf flow pair."""
        ...
    
    @abstractmethod
    def start_fixed_iperf_pair(self, *args, **kwargs) -> None:
        """Start a fixed-duration iperf flow pair."""
        ...
    
    @abstractmethod
    def cleanup(self, loop: asyncio.AbstractEventLoop) -> None:
        """Clean up resources when experiment ends."""
        ...
```

##### 2. `MininetCommunicator`

Handles traffic generation in Mininet emulated environments:

```python
class MininetCommunicator(Communicator):
    """
    Communicator for Mininet-based network emulation.
    
    Methods:
        - host_cmd(): Execute command on Mininet host
        - host_popen(): Start process on Mininet host
        - start_iperf_pair(): Start iperf client/server pairs
        - start_fixed_iperf_pair(): Start fixed-duration flows
        - cleanup(): Kill all iperf processes
    """
```

##### 3. `APICommunicator`

Handles traffic generation on hardware testbeds via HTTP API:

```python
class APICommunicator(Communicator):
    """
    Communicator for hardware testbed via REST API.
    
    Key features:
        - Port pool management
        - Automatic port recycling
        - Batch request handling
        - API server communication
    """
```

#### command_utils.py

Provides utility functions for configuration validation and command execution:

| Function | Description |
|----------|-------------|
| `_create_exp_dir()` | Creates unique experiment directories |
| `fast_random_choice()` | Optimized random sampling with weights |
| `_parse_duration()` | Parses duration strings (e.g., "5s", "2m", "1h") |
| `_process_intervals()` | Processes configuration intervals |
| `file_checker()` | Validates configuration files |
| `execute_command()` | Executes SSH commands via Nornir |

#### distance_seperate.py

Provides topology analysis and path distance classification:

```python
def distance_partition(hosts=None, ndtwin_server=None):
    """
    Partition host pairs into {near, middle, far} categories
    based on network path length.
    
    Returns:
        partition: Dict with 'near', 'middle', 'far' host pairs
        hosts: Dict mapping IP addresses to host names
    """
```

---

## Key Design Patterns

### 1. Asyncio-Based Concurrency

NTG uses Python's `asyncio` for concurrent operations:

```python
async def _run_custom_command_loop(net):
    """
    Example of async command loop pattern.
    """
    session = PromptSession(history=InMemoryHistory())
    
    with patch_stdout():
        while True:
            line = await session.prompt_async("NTG> ")
            # Process command...
```

### 2. Abstract Factory Pattern (Communicators)

```python
# Select communicator based on configuration
if NTG_CONFIG.inventory.hosts.get("Mininet_Testbed"):
    INTERFACE = MininetCommunicator(logger=logger, on_flow_finished=callback)
elif NTG_CONFIG.inventory.hosts.get("Hardware_Testbed"):
    INTERFACE = APICommunicator(logger=logger, on_flow_finished=callback)
```

### 3. Callback Pattern for Flow Tracking

```python
async def _on_flow_finished(t) -> None:
    """Callback invoked when flows complete."""
    global RUNNING
    async with LOCK:
        RUNNING -= t
        if RUNNING < 0:
            RUNNING = 0
```

### 4. Pydantic Models for Data Validation

```python
class SenderReq(BaseModel):
    """Request model for iperf sender configuration."""
    c: str                          # Target IP
    port: int                       # Target port
    u: bool = False                 # UDP mode
    b: Optional[str] = None         # Bandwidth limit
    n: Optional[str] = None         # Number of bytes
    t: Optional[int] = None         # Duration
```

---

## Developing New Features

### Adding New Commands

To add a new command to the NTG CLI:

#### Step 1: Add Command to Auto-completer

In `interactive_commands.py`, modify the `ConcateCompleter` class:

```python
class ConcateCompleter(Completer):
    def __init__(self):
        self.completer = NestedCompleter.from_nested_dict({
            "dist": {"--config": None},
            "flow": {"--config": None},
            "exit": None,
            # Add your new command here:
            "mycommand": {"--option1": None, "--option2": None},
        })
```

#### Step 2: Create Command Handler

Create an async handler function:

```python
async def _handle_mycommand(net, args):
    """
    Handle the 'mycommand' command.
    
    Args:
        net: Mininet network instance
        args: Command arguments list
    """
    global RUNNING, INTERFACE
    
    # Parse arguments
    if "--option1" in args:
        option1_value = args[args.index("--option1") + 1]
    
    # Implement your logic here
    logger.info(f"Executing mycommand with option1={option1_value}")
    
    # Use INTERFACE to interact with network
    await INTERFACE.start_iperf_pair(...)
```

#### Step 3: Register Command in Main Loop

In `_run_custom_command_loop()`, add the command routing:

```python
async def _run_custom_command_loop(net):
    # ... existing code ...
    
    if cmd == 'mycommand':
        await _handle_mycommand(net, args)
        continue
    
    # ... rest of commands ...
```

#### Complete Example: Adding a "ping" Command

```python
# Step 1: Update completer
self.completer = NestedCompleter.from_nested_dict({
    # ... existing commands ...
    "ping": {"--src": None, "--dst": None, "--count": None},
})

# Step 2: Create handler
async def _handle_ping_command(net, args):
    """Execute ping between two hosts."""
    src = args[args.index("--src") + 1] if "--src" in args else "h1"
    dst = args[args.index("--dst") + 1] if "--dst" in args else "h2"
    count = args[args.index("--count") + 1] if "--count" in args else "4"
    
    if type(INTERFACE) == MininetCommunicator:
        src_host = net.get(src)
        dst_host = net.get(dst)
        result = await INTERFACE.host_cmd(src_host, f"ping -c {count} {dst_host.IP()}")
        logger.info(f"Ping result:\n{result}")
    else:
        logger.warning("Ping command only available in Mininet mode")

# Step 3: Add to command loop
if cmd == 'ping':
    await _handle_ping_command(net, args)
    continue
```

---

### Creating New Communicators

To support a new testbed type (e.g., Docker containers):

#### Step 1: Create New Communicator Class

```python
# In Utilis/communicator.py

class DockerCommunicator(Communicator):
    """
    Communicator for Docker container-based testbed.
    """
    
    def __init__(self, logger, on_flow_finished=None, docker_client=None):
        super().__init__("Docker")
        self.logger = logger
        self.on_flow_finished = on_flow_finished
        self.docker_client = docker_client or docker.from_env()
    
    async def start_iperf_pair(
        self,
        net,  # Not used in Docker mode
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
        """Start iperf pairs in Docker containers."""
        for src, dst, params in zip(src_list, dst_list, parameter_list):
            # Get container references
            src_container = self.docker_client.containers.get(src)
            dst_container = self.docker_client.containers.get(dst)
            
            # Start server
            dst_ip = self._get_container_ip(dst_container)
            server_cmd = f"{iperf} -s -1 -p 5201"
            dst_container.exec_run(server_cmd, detach=True)
            
            await asyncio.sleep(0.01)
            
            # Start client
            param_str = " ".join(f"{k} {v}" for k, v in params.items())
            client_cmd = f"{iperf} -c {dst_ip} {param_str} -p 5201"
            src_container.exec_run(client_cmd, detach=True)
            
            self.logger.trace(f"Started flow {src} -> {dst}")
        
        if self.on_flow_finished:
            await self.on_flow_finished(len(src_list))
    
    async def start_fixed_iperf_pair(self, *args, **kwargs) -> None:
        """Start fixed-duration iperf pairs."""
        # Implementation similar to start_iperf_pair
        pass
    
    async def cleanup(self, loop: asyncio.AbstractEventLoop, leave: bool = False):
        """Clean up Docker iperf processes."""
        for container in self.docker_client.containers.list():
            container.exec_run("killall iperf3", detach=True)
    
    def _get_container_ip(self, container) -> str:
        """Get container's IP address."""
        networks = container.attrs['NetworkSettings']['Networks']
        return list(networks.values())[0]['IPAddress']
```

#### Step 2: Register Communicator in Main Module

In `interactive_commands.py`:

```python
from Utilis.communicator import MininetCommunicator, APICommunicator, DockerCommunicator

def interactive_command_mode(net, config_file_path="NTG.yaml"):
    global INTERFACE
    
    # ... existing code ...
    
    if NTG_CONFIG.inventory.hosts.get("Docker_Testbed") is not None:
        docker_config = NTG_CONFIG.inventory.hosts["Docker_Testbed"].data
        INTERFACE = DockerCommunicator(
            logger=logger,
            on_flow_finished=_on_flow_finished,
        )
        logger.info("Using Docker Communicator")
```

---

### Extending Utility Functions

#### Adding New Duration Units

In `command_utils.py`, extend `_parse_duration()`:

```python
def _parse_duration(interval: list):
    """Parse duration strings with extended unit support."""
    
    time_units = {
        's': 1, 'sec': 1, 'second': 1, 'seconds': 1,
        'm': 60, 'min': 60, 'minute': 60, 'minutes': 60,
        'h': 3600, 'hour': 3600, 'hours': 3600,
        'd': 86400, 'day': 86400, 'days': 86400,
        # Add new units:
        'w': 604800, 'week': 604800, 'weeks': 604800,
        'ms': 0.001, 'millisecond': 0.001,
    }
    # ... rest of implementation
```

#### Adding New Validation Rules

```python
def check_custom_parameter(traffic: dict, param_name: str) -> Optional[str]:
    """
    Custom validation for new parameters.
    
    Args:
        traffic: Traffic configuration dictionary
        param_name: Name of parameter to validate
        
    Returns:
        Error message if invalid, None if valid
    """
    value = traffic.get(param_name)
    
    if value is None:
        return f"Missing required parameter: {param_name}"
    
    if not isinstance(value, (int, float)):
        return f"{param_name} must be numeric"
    
    if value < 0:
        return f"{param_name} must be non-negative"
    
    return None
```

---

## API Reference

### Communicator Interface

| Method | Parameters | Returns | Description |
|--------|------------|---------|-------------|
| `start_iperf_pair` | net, src_list, dst_list, parameter_list, times, ... | None | Start dynamic iperf flows |
| `start_fixed_iperf_pair` | net, src_list, dst_list, parameter_list, times, fixed_duration, ... | None | Start fixed-duration flows |
| `cleanup` | loop, leave | None | Clean up resources |

### MininetCommunicator Methods

| Method | Parameters | Returns | Description |
|--------|------------|---------|-------------|
| `host_cmd` | host, command | str | Execute command on host |
| `host_popen` | host, argv | Process | Start process on host |
| `process_wait` | process | (stdout, stderr) | Wait for process completion |
| `process_terminate` | process | None | Terminate process |

### APICommunicator Methods

| Method | Parameters | Returns | Description |
|--------|------------|---------|-------------|
| `port_selector` | dst | int | Get available port for host |
| `recycle_port` | - | None | Background port recycling task |
| `reset_for_new_experiment` | - | None | Reset state for new experiment |
| `check_restore_ports` | - | bool | Check if all ports restored |

### Utility Functions

| Function | Module | Description |
|----------|--------|-------------|
| `_create_exp_dir(path)` | command_utils | Create unique experiment directory |
| `fast_random_choice(arr, weights, size)` | command_utils | Weighted random sampling |
| `file_checker(data, type)` | command_utils | Validate configuration file |
| `distance_partition(hosts, server)` | distance_seperate | Classify host pairs by distance |

---

## Best Practices

### 1. Async Programming Guidelines

```python
# ✅ Good: Use async/await properly
async def my_function():
    result = await some_async_operation()
    return result

# ❌ Bad: Blocking in async context
async def my_function():
    result = blocking_operation()  # Blocks event loop!
    return result

# ✅ Good: Use run_in_executor for blocking operations
async def my_function():
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, blocking_operation)
    return result
```

### 2. Logging Best Practices

```python
from loguru import logger

# Use appropriate log levels
logger.trace("Detailed debugging info")    # Very verbose
logger.debug("Debugging information")      # Debug mode
logger.info("General information")         # Normal operation
logger.success("Operation succeeded")      # Success messages
logger.warning("Warning message")          # Non-critical issues
logger.error("Error occurred")             # Errors
```

### 3. Error Handling

```python
async def robust_operation():
    try:
        result = await risky_operation()
        return result
    except ConnectionError as e:
        logger.warning(f"Connection failed: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise
    finally:
        await cleanup_resources()
```

### 4. Resource Management

```python
# Always clean up resources
async def experiment():
    try:
        await run_experiment()
    finally:
        await INTERFACE.cleanup(asyncio.get_running_loop())
```

### 5. Thread Safety

```python
# Use locks for shared state
async def update_counter():
    global RUNNING
    async with LOCK:
        RUNNING += 1
```

---

## Troubleshooting

### Common Issues

#### Issue: "Port already in use"

**Cause**: Previous iperf processes not cleaned up properly.

**Solution**:
```python
# Manual cleanup
await INTERFACE.cleanup(asyncio.get_running_loop())

# Or via terminal
# sudo killall -s SIGTERM iperf3
```

#### Issue: "Flows not starting"

**Cause**: Host names not matching topology.

**Solution**:
```python
# Debug host mapping
logger.debug(f"Available hosts: {HOSTS}")
logger.debug(f"Connections: {CONNECTIONS}")
```

### Debug Mode

Enable verbose logging:

```python
# In interactive_commands.py
logger.add(
    sys.stdout,
    level="TRACE"  # Most verbose
)
```

### Testing New Features

```python
# Create test script
import asyncio
from Utilis.communicator import MininetCommunicator

async def test_new_feature():
    comm = MininetCommunicator(logger=logger)
    
    # Test your implementation
    await comm.new_method(test_params)
    
    # Verify results
    assert expected_condition
    
if __name__ == "__main__":
    asyncio.run(test_new_feature())
```

---

## Appendix: Flow Type Reference

| Flow Type | Parameters | Description |
|-----------|------------|-------------|
| `unlimited_size_unlimited_rate_unlimited_duration_tcp` | `-un` | Continuous TCP flow |
| `unlimited_size_unlimited_rate_limited_duration_tcp` | `-t` | Time-limited TCP flow |
| `limited_size_unlimited_rate_tcp` | `-n` | Size-limited TCP flow |
| `unlimited_size_limited_rate_unlimited_duration_tcp` | `-b`, `-un` | Rate-limited continuous TCP |
| `unlimited_size_limited_rate_limited_duration_tcp` | `-b`, `-t` | Rate and time limited TCP |
| `limited_size_limited_rate_tcp` | `-n`, `-b` | Size and rate limited TCP |
| `*_udp` | Same as TCP + `-u` | UDP variants of above |

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2024 | Initial release |

---

*For questions or contributions, please refer to the project repository.*
