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

Get current hosts in topology and paths of host pair.

Partition the unique paths array to {near,middle,far} set.

"""
import requests
import json
from collections import deque
import ipaddress
import numpy as np

GET_HOSTS = ""
GET_PATHS = ""


def get_hosts():
    """
    Get host's ip from /get_graph_data api.
    """
    try:
        response = requests.get(GET_HOSTS)
        if response.status_code == 200:
            response = json.loads(response.text)
            nodes = response["nodes"]
            hosts = {}
            for node in nodes:
                if node["vertex_type"] == 1: # is host
                    for i,host in enumerate(node["ip"]):
                        if isinstance(host, int) and 0 <= host <= 0xFFFFFFFF:
                            ip_str = str(ipaddress.IPv4Address(host.to_bytes(4, 'little', signed=False)))
                        else:
                            ip_str = str(ipaddress.ip_address(host))

                        device_name = f"h{ (int(node['device_name'][1:])-1)*(len(node['ip'])) + (i+1) }"
                        hosts.update({ip_str: device_name})
                        
            return hosts
        return None
    except Exception:
        return [-1]

def distance_partition(hosts = None, ndtwin_server=None):
    """
    Partition the unique paths array to {near,middle,far} set.
    """
    global GET_PATHS,GET_HOSTS

    if ndtwin_server is not None:
        GET_PATHS = ndtwin_server+"/ndt/get_path_switch_count"
        GET_HOSTS = ndtwin_server+"/ndt/get_graph_data"

    if hosts is None:
        hosts = get_hosts()
    
    if -1 in hosts:
        return False,False
    
    unique_paths = {}

    # get all paths from api.
    response = requests.get(GET_PATHS)
    if response.status_code == 200:
        response = json.loads(response.text)
        paths = response["data"]
        for path in paths:
            client = {"ip":path["src_ip"],"name":hosts[path["src_ip"]]}
            server = {"ip":path["dst_ip"],"name":hosts[path["dst_ip"]]}
            switch_count = path["switch_count"]
            unique_paths.setdefault(switch_count, []).append((client, server))
    else:
        raise ConnectionError("NDTwin Server not up...")
    
    # partition the paths

    unique_paths = {k:unique_paths[k] for k in sorted(unique_paths)}


    points = partition_method(unique_paths.keys())

    partition = {"near":[],"middle":[],"far":[]}
    start = 0

    for point,par in zip(points, partition): # point's index element is in the same cluster
        segments = [{'src':pair[0],'dst':pair[1]} for _, pairs in list(unique_paths.items())[start:point] for pair in pairs]
        partition[par].extend(segments)
        start = point
    
    return partition,hosts


def CC(x):
    """
    Calculate the cost function for 1-D K-Means clustering.
    """
    m = np.mean(x)
    d = (x - m)**2
    return np.sum(d)

def partition_method(unique_paths):
    """
    Partition the unique paths array to {near,middle,far} set using 1-D K-Means clustering.
    3 clusters: near, middle, far
    """
    unique_paths = list(unique_paths)
    n =  len(unique_paths)
    
    # implement 1-D K-Means from "https://arxiv.org/abs/1701.07204", DP method with complexity O(kn^2)

    D = np.zeros((3+1,n+1))
    T = np.zeros((3+1,n+1))

    for m in range(1,n+1):
        D[1,m] = CC(unique_paths[0:m])

    for i in range(2,4):
        for m in range(i,n+1):
            D_ms = np.array([D[i-1, j-1] for j in range(2,m+1)])
            CC_ms = np.array([CC(unique_paths[j-1:m]) for j in range(2,m+1)])
            ms = D_ms + CC_ms
            D[i,m] = np.min(ms)
            T[i,m] = np.argmin(ms)+1

        
    m = n
    T = T.astype(int)
    # Find the borders of clusters
    cluster_boards = deque()
    for i in range(3,0,-1): # 3 -> 1
        m = T[i,m]
        cluster_boards.appendleft(m)
    cluster_boards.append(n)

    # Create an array of length n where ith element is assigned to cluster
    clusters = []
    l = cluster_boards.popleft()
    while cluster_boards:
        r =  cluster_boards.popleft()
        clusters.append(int(r))
    return clusters

if __name__ == '__main__':
    print(distance_partition())
