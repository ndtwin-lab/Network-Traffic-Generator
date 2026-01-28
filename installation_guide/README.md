# Network Traffic Generator — Installation Manual

## Table of Content

- [Network Traffic Generator — Installation Manual](#network-traffic-generator--installation-manual)
  - [Table of Content](#table-of-content)
  - [NTG Requirements](#ntg-requirements)
  - [NTG for Mininet Environment](#ntg-for-mininet-environment)
  - [NTG for Hardware Testbed](#ntg-for-hardware-testbed)


## NTG Requirements

- **Linux** system is needed for all machines.
- Python 3.8+ recommended.

Install Python + pip:

Ubuntu/Debian:

```bash
sudo apt update
sudo apt install -y python3 python3-pip
python3 --version
pip3 --version
```

Libraries used by NTG controller-side scripts (`network_traffic_generator.py` + `Utilis/*.py`):

- `loguru`
- `prompt_toolkit`
- `nornir`, `nornir-utils`
- `pyyaml`
- `numpy`, `pandas`
- `paramiko`
- `requests`

Install them:

```bash
pip install --upgrade pip
pip install loguru prompt_toolkit nornir nornir-utils pyyaml numpy pandas paramiko requests
```

Clone our NTG:

```bash
git clone https://github.com/ndtwin-lab/Network-Traffic-Generator.git
```

If your environment uses Conda/virtualenv, activate it before installing packages.

## NTG for Mininet Environment

Suppose you use Mininet topology code as blow:

```python
from mininet.topo import Topo
from mininet.net import Mininet
from mininet.cli import CLI
from mininet.node import RemoteController

class MyTopo(Topo):
  def build(self):
    s1 = self.addSwitch("s1")
    h1 = self.addHost("h1")
    h2 = self.addHost("h2")

    self.addLink(h1,s1)
    self.addLink(h2,s1)

if __name__ == "__main__":
  topo = MyTopo()
  net = Mininet(
        topo=topo,
        controller=RemoteController)
  net.start()
  CLI(net)
```

You must import `network_traffic_generator.py` and change the `CLI(net)` to `command_line(net)` as below:

```python
from mininet.topo import Topo
from mininet.net import Mininet
#from mininet.cli import CLI
from mininet.node import RemoteController
from network_traffic_generator import command_line

class MyTopo(Topo):
  def build(self):
    s1 = self.addSwitch("s1")
    h1 = self.addHost("h1")
    h2 = self.addHost("h2")

    self.addLink(h1,s1)
    self.addLink(h2,s1)

if __name__ == "__main__":
  topo = MyTopo()
  net = Mininet(
        topo=topo,
        controller=RemoteController)
  net.start()
  #CLI(net)
  **command_line(net)**
```

After that, you can follow the [NTG User Manual](../README.md) to generate flows on Mininet using NTG.

## NTG for Hardware Testbed

For hardware testbed, we use **master and worker** architecture to generate flows. Thus, you need to prepare some machines running in **Linux and install python libraries** as below and **move `network_traffic_generator_worker_node.py` into those machines**:

- `fastapi`
- `uvicorn` (used to start the API server)
- `pydantic`
- `loguru`
- `orjson` (required by `ORJSONResponse`)

```bash
pip install --upgrade pip
pip install fastapi "uvicorn[standard]" pydantic loguru orjson
```

Also, you need to make sure NTG can connect to those worker nodes.
