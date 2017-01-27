'''
Module to create HDFS performance testing environment 

Master node runs HDFS NameNode and YARN Resource manager daemons.
Worker nodes are where data is stored and computations are performed. They run HDFS DataNode and YARN NodeManager daemons.
'''

from __future__ import print_function

import os
import os.path
import collections

from linode_core import Core, Linode
from provisioners import AnsibleProvisioner

import simplejson as json

import logger


def create_cluster(name, datacenter):
    
    test_cluster = load_cluster(name)
    if test_cluster is not None:
        print('Cluster %s already exists. Use a different name or load this one instead of creating.' % (name))
        return None
        
    cluster = collections.OrderedDict()
    cluster['name'] = name
    cluster['dc'] = datacenter
    cluster['master'] = {}
    cluster['workers'] = []
    
    save_cluster(cluster)
        
    return cluster
    

def add_master_node(name):
    
    cluster = load_cluster(name)

    app_ctx = {'conf-dir' : conf_dir()}
    core = Core(app_ctx)
    
    master_linode_spec = {
            'plan_id' : 1, # Linode 2 GB RAM node
            'datacenter' : cluster['dc'],
            'distribution' : 'Ubuntu 14.04 LTS',
            'kernel' : 'Latest 64 bit',
            'label' : 'hdpmaster',
            'group' : 'hdfsperftests',
            'disks' :   {
                            'boot' : {'disk_size' : 2*1024},
                            'swap' : {'disk_size' : 512},
                            'others' :  [
                                        {
                                            'label' : 'hdfs',
                                            'disk_size' : 21 * 1024,
                                            'type' : 'ext4'
                                        }
                                        ]
                            
                        }

    }
        
    linode = core.create_linode(master_linode_spec)
    if not linode:
        logger.error_msg('Could not create master node')
        return None
    
    # Save master node details to cluster.
    master = collections.OrderedDict()
    master['id'] = linode.id
    master['public_ip'] = str(linode.public_ip[0])
    master['private_ip'] = linode.private_ip
    master['fqdn'] = 'hdpmaster.' + cluster['name']
    master['shortname'] = 'hdpmaster'
    
    cluster['master'] = master
    
    save_cluster(cluster)
    
    
def provision_master_node(name):
    
    cluster = load_cluster(name)
    master = cluster['master']
    
    prov = AnsibleProvisioner()
    
    master_ip = master['public_ip']
    
    # Wait for SSH service on linode to come up.
    temp = Linode()
    temp.public_ip = [ master_ip ]
    if not prov.wait_for_ping(temp, 60, 10):
        print("Unable to reach %s over SSH" % (master_ip))
        return None
    
    print('Provisioning master node')
    
    # Set the node's hostname. No underscrores allowed in hostname.
    master['hostname'] = 'hdpmasterlocal'
    prov.exec_playbook(master_ip, 'ansible/change_hostname.yaml',
        variables = {
            'new_hostname' : master['hostname']
        })

    pubkey_dir = os.path.join(conf_dir(), cluster['name'], 'pubkeys') 
    if not os.path.exists(pubkey_dir):
        os.makedirs(pubkey_dir)
    
    pubkey_file = os.path.abspath( os.path.join(pubkey_dir, master['fqdn'] + '.pub' ) )
    prov.exec_playbook(master_ip, 'ansible/hadoop.yaml',
        variables = {
            # If path does not end with a /, this becomes the name of the downloaded file
            # instead of the directory under which it should be saved.
            'master_node_fqdn' : master['fqdn'],
            'worker_node_fqdn' : '',
            'local_pubkey_file' : pubkey_file
        })

    if os.path.isfile(pubkey_file):
        with open(pubkey_file, 'r') as f:
            pubkey = f.read().strip('\n')
            
        master['pubkey'] = pubkey
        
    else:
        print('Error: public key %s not found' % (pubkey_file))
    
    # The master node should be able to SSH to itself, because some of the hadoop
    # start/stop scripts require it.
    prov.exec_playbook(master_ip, 'ansible/add_authorized_keys.yaml',
        variables = {
            'keys' : master['pubkey'] + '\n',
            'cluster' : cluster['name']
        })

    
    save_cluster(cluster)
        
    
def add_worker_node(name):
    
    cluster = load_cluster(name)
    
    app_ctx = {'conf-dir' : conf_dir()}
    core = Core(app_ctx)
    
    worker_index = len(cluster['workers']) + 1
    
    label = 'hdpworker-%d' % (worker_index)

    worker_linode_spec = {
            'plan_id' : 7, # Linode 384 GB storage, 2Mbps outgoing network
            'datacenter' : cluster['dc'],
            'distribution' : 'Ubuntu 14.04 LTS',
            'kernel' : 'Latest 64 bit',
            'label' : label,
            'group' : 'hdfsperftests',
            'disks' :   {
                            'boot' : {'disk_size' : 10*1024},
                            'swap' : {'disk_size' : 2*1024},
                            'others' :  [
                                        {
                                            'label' : 'hdfs',
                                            'disk_size' : 372 * 1024,
                                            'type' : 'ext4'
                                        }
                                        ]
                            
                        }

    }
        
    linode = core.create_linode(worker_linode_spec)
    if not linode:
        logger.error_msg('Could not create worker node')
        return None
    
    # Save admin node details to cluster under both admin and monitor node keys.
    worker = collections.OrderedDict()
    worker['id'] = linode.id
    worker['public_ip'] = str(linode.public_ip[0])
    worker['private_ip'] = linode.private_ip
    worker['fqdn'] = 'hdpworker-%d.%s' % (worker_index, cluster['name'])
    worker['shortname'] = 'hdpworker-%d' % (worker_index) 
    
    cluster['workers'].append(worker)
    
    save_cluster(cluster)
    
    
def provision_worker_node(name, index):
    
    cluster = load_cluster(name)
    worker = cluster['workers'][index]
    
    prov = AnsibleProvisioner()
    
    worker_ip = worker['public_ip']
    
    # Wait for SSH service on linode to come up.
    temp = Linode()
    temp.public_ip = [ worker_ip ]
    if not prov.wait_for_ping(temp, 60, 10):
        print("Unable to reach %s over SSH" % (worker_ip))
        return None
    
    print('Provisioning worker node')
    
    # Set the node's hostname. No underscrores allowed in hostname.
    worker['hostname'] = 'hdpworkerlocal-%d' % (index + 1 if index >= 0 else len(cluster['workers']) + index + 1)
    prov.exec_playbook(worker_ip, 'ansible/change_hostname.yaml',
        variables = {
            'new_hostname' : worker['hostname']
        })

    pubkey_dir = os.path.join(conf_dir(), cluster['name'], 'pubkeys') 
    if not os.path.exists(pubkey_dir):
        os.makedirs(pubkey_dir)
    
    pubkey_file = os.path.abspath( os.path.join(pubkey_dir, worker['fqdn'] + '.pub' ) )
    prov.exec_playbook(worker_ip, 'ansible/hadoop.yaml',
        variables = {
            # If path does not end with a /, this becomes the name of the downloaded file
            # instead of the directory under which it should be saved.
            'master_node_fqdn' : cluster['master']['fqdn'],
            'worker_node_fqdn' : worker['fqdn'],
            'local_pubkey_file' : pubkey_file
        })

    if os.path.isfile(pubkey_file):
        with open(pubkey_file, 'r') as f:
            pubkey = f.read().strip('\n')
            
        worker['pubkey'] = pubkey
        
    else:
        print('Error: public key %s not found' % (pubkey_file))

    # Save worker details to cluster file.
    save_cluster(cluster)
    
    # The master node should be able to SSH to itself, because some of the hadoop
    # start/stop scripts require it.
    prov.exec_playbook(worker_ip, 'ansible/add_authorized_keys.yaml',
        variables = {
            'keys' : cluster['master']['pubkey'] + '\n',
            'cluster' : cluster['name']
        })
        
    # Update /etc/hosts on all nodes, because next playbook requires worker FQDNs to be resolvable
    # on master.
    update_fqdn_entries(name)
    
        
    # Add this worker to known_hosts on master, because some of the hadoop
    # start/stop scripts SSH to workers.
    prov.exec_playbook(cluster['master']['public_ip'], 'ansible/add_known_host.yaml',
        variables = {
            'host_fqdn' : worker['fqdn']
        })

        
    

    
def update_fqdn_entries(name):
    
    cluster = load_cluster(name)
    
    # Update /etc/hosts on all nodes of cluster to include all nodes.
    
    prov = AnsibleProvisioner()
    
    host_entries = []
    targets = []
    
    # Add entry for master node.
    host_entries.append( 
        {   'ip' : cluster['master']['private_ip'], 
            'fqdn' : cluster['master']['fqdn'],
            'shortname' : cluster['master']['shortname'] 
        })
    targets.append(cluster['master']['public_ip'])


    # Add entries for worker nodes.
    for w in cluster['workers']:
        host_entries.append( 
            {   'ip' : w['private_ip'], 
                'fqdn' : w['fqdn'],
                'shortname' : w['shortname'] 
            })
            
        targets.append(w['public_ip'])
    
    
    prov.exec_playbook(targets, 'ansible/modify_hosts_file.yaml',
        variables = {
            'host_entries' : host_entries,
            'cluster' : cluster['name']
        })
            
    
    
def load_cluster(name):
    
    the_conf_dir = conf_dir()
    cluster_file = os.path.join(the_conf_dir, name, name + '.json')
    if not os.path.isfile(cluster_file):
        return None

    with open(cluster_file, 'r') as f:
        cluster = json.load(f, object_pairs_hook=collections.OrderedDict)
    
    return cluster
    




def save_cluster(cluster):
    
    the_conf_dir = conf_dir()
    
    cluster_dir = os.path.join(the_conf_dir, cluster['name'])
    if not os.path.exists(cluster_dir):
        os.makedirs(cluster_dir)
    
    cluster_file = os.path.join(cluster_dir, cluster['name'] + '.json')
    with open(cluster_file, 'w') as f:
        json.dump(cluster, f, indent = 4 * ' ')        
    
    
    
def conf_dir() :
    return './hdfsperfdata'
    


if __name__ == '__main__':
    
    name = 'hdfsperfcluster'
    #name = 'vmtest'
    
    #cluster = create_cluster(name, 6) # 6 is Newark, NJ DC
    
    #add_master_node(name)
    
    #provision_master_node(name)
    
    add_worker_node(name)
    
    #provision_worker_node(name, 0)
    provision_worker_node(name, 1)
    
    #update_fqdn_entries(name)
    
