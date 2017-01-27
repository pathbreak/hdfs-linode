'''
HDFS cluster creation wizard with a console UI.
'''

from __future__ import print_function

import sys
import re
import math
import os
from os import path
import simplejson as json
import collections

from linodecommon import logger
from linodecommon import linode_api
from terminaltables import AsciiTable, SingleTable

class MainWizard(object):
    
    def __init__(self):
        
        self.main_menu = Menu('HDFS Cluster Manager', actions = [
            {'selector' : '1', 'title' : 'Create new HDFS cluster', 'callback' : MainWizard.create_new_cluster},
            {'selector' : '2', 'title' : 'List HDFS clusters', 'callback' : MainWizard.list_clusters},
            {'selector' : '3', 'title' : 'Manage HDFS cluster', 'callback' : MainWizard.manage_cluster},
            {'selector' : 'q', 'title' : 'Quit', 'callback' : MainWizard.quit}
        ])
        
        
    def start(self):
        while True:
            self.main_menu.select(self)
        
        
    def create_new_cluster(self):
        create_wizard = ClusterCreationWizard()
        create_wizard.start()
        
    
    def list_clusters(self):
        pass
        
    def manage_cluster(self):
        pass
        
    def quit(self):
        logger.success_msg("Goodbye!!")
        sys.exit(0)
        
        
        
class ClusterCreationWizard(object):
    
    def __init__(self):
        self.cluster = {}
        
        # Get plan information either saved previously or directly from API.
        # plan['DISK'] is in GB, plan['RAM'] is in MB, plan['HOURLY'] is the hourly rate,
        # plan['PRICE'] is the monthly rate
        # plan['XFER']is in GB
        plan_info_file = 'plans.json'
        if os.path.exists(plan_info_file):
            with open(plan_info_file, 'r') as f:
                self.plans = json.load(f, object_pairs_hook = collections.OrderedDict)
        else:
            self.plans = linode_api.get_plans()
            with open(plan_info_file, 'w') as f:
                json.dump(self.plans, f, indent=4 * ' ')
        
        # Default disk allocations for each plan ID.
        # The list entries are boot, swap, hdfsdata - all in GB.
        # key is the DISK entry of each plan
        self.default_disk_plans = {
            24 : [5,1,18],
            48 : [8,1,39],
            96 : [12,1,83],
            192 : [20,2,170],
            384 : [20,4,362],
            768 : [20,4,746],
            1152 : [20,8,1124],
            1536 : [20,8,1508],
            1920 : [20,8,1892]
        }
    
    
    def start(self):
        
        logger.heading('\nCreate New HDFS Cluster')
        
        # Get initial storage capacity.
        modify_capacity = True
        while modify_capacity:
            
            self.get_cluster_capacity()
            ret = InputUtils.get(
                'Do you want to modify the capacity? (y/n, default n)',
                ValidatorUtils.validate_yesno, None, 'n')
            modify_capacity = ret[1]
            
        # Get plan for storage nodes.
        self.get_storage_plans()
        
        # Get NameNode strategy.
        self.get_namenode_strategy()
        
        
        
        
    def get_cluster_capacity(self):
        
        print()
        
        # Get initial storage capacity
        storage_capacity = '1 TB'
        storage_capacity = InputUtils.get(
            'How much storage capacity do you want to start with ' + 
            ' (not including copies)? [default=%s] : ' % (storage_capacity), 
            ValidatorUtils.validate_storage_size, None, storage_capacity)
            
        self.cluster['initial_capacity'] = storage_capacity[1]
        
        # Get replication factor. number of copies = Replication factor - 1
        copies = 2
        min_copies = 0
        max_copies = 511 # https://hadoop.apache.org/docs/current/hadoop-project-dist/hadoop-hdfs/hdfs-default.xml
        copies = InputUtils.get(
            'How many additional copies of each file should be stored? [%d-%d, default=%d] : ' 
            % (min_copies, max_copies, copies), 
            ValidatorUtils.validate_int, (min_copies, max_copies), copies)
            
        self.cluster['copies'] = copies[1]
        
        # Calculate total storage capacity = initial_capacity * (copies+1)
        total_capacity_mb = storage_capacity[1]['size_in_mb'] * (copies[1] + 1)
        total_capacity_str = Utils.mb_to_units(total_capacity_mb)
        self.cluster['total_initial_capacity'] = total_capacity_mb
        self.cluster['total_initial_capacity_display'] = total_capacity_str
        
        logger.warn_msg('Total Storage capacity: ' + total_capacity_str)
        
        
        
    def get_storage_plans(self):
        
        logger.msg("\nLet's now select hardware configurations of the storage nodes to store " + 
            self.cluster['total_initial_capacity_display'] + ':')
        
        
        # To show approximate costs with each plan, assume an initial reasonable disk
        # allocation. Once user selects the plans, ask if disk allocation 
        # should be changed, and if so, update costs with new allocation.
        
        table_data = [
            ['ID','Plan','Storage/node\nGB *','Nodes','$/node', '$/month','$/hour',
             'Cores', 'RAM(GB)', 'Excess\nStorage GB','Free Outgoing\nTB/month']
        ]
        all_plan_ids = []
        plan_info = {}
        for p in self.plans[-1::-1]:
            plan_id = p['PLANID']
            all_plan_ids.append(str(plan_id))
            
            plan_storage_gb = p['DISK']
            
            disk_plan = self.default_disk_plans[plan_storage_gb]
            
            usable_storage = disk_plan[2] * 1024 # GB to MBs
            
            node_count = int( math.ceil( self.cluster['total_initial_capacity'] / float(usable_storage) ) )
            
            total_monthly_cost = node_count * p['PRICE']
            total_hourly_cost = node_count * p['HOURLY']
            
            storage = '{:d} ({:s})'.format(p['DISK'], '+'.join( str(d) for d in disk_plan ))
            
            cores = p['CORES']
            ram = p['RAM'] / 1024
            
            # Excess storage says by how much the total usable storage of all nodes exceeds the requested storage capacity.
            # At max, it'll be around the usable storage of 1 node.
            excess_storage = (node_count * usable_storage - self.cluster['total_initial_capacity']) / (1024)
            
            plan_info[plan_id] = {
                'plan_name' : p['LABEL'],
                'count' : node_count,
                'total_monthly_cost' : total_monthly_cost,
                'total_hourly_cost' : total_hourly_cost
            }
            
            table_data.append( [
                p['PLANID'], 
                p['LABEL'], 
                storage, 
                node_count, 
                p['PRICE'],
                '{:,.2f}'.format(total_monthly_cost), 
                '{:,.2f}'.format(total_hourly_cost),
                '{:,d}'.format(cores),
                '{:,d}'.format(ram),
                '{:,d}'.format(excess_storage),
                '{:,d}'.format(node_count * p['XFER']/1024)
            ])

        table = SingleTable(table_data)
        print(table.table)
        print('*Storage: Total storage available and its allocation (Boot + Swap + HDFS storage)')
        
        # TODO It's possible to allow mixed plans input here by asking for plan + count
        # multiple times, and calculating remaining capacity at each step.
        
        selection_confirmed = False
        while not selection_confirmed:
            ret = InputUtils.get(
                '\nSelect a plan and type its ID: ', 
                ValidatorUtils.validate_set, all_plan_ids, None)
                
            storage_plan_id = int(ret[1])

            ret = InputUtils.get(
                ('\nThe cluster will have {:d} {:s} storage nodes.\n' + 
                'Total monthly cost: ${:,.2f}. Total hourly cost: ${:,.2f}. Continue with this selection? (y/n) (default=y): ').format(
                    plan_info[storage_plan_id]['count'],
                    plan_info[storage_plan_id]['plan_name'],
                    plan_info[storage_plan_id]['total_monthly_cost'],
                    plan_info[storage_plan_id]['total_hourly_cost']
                ),
                ValidatorUtils.validate_yesno, None, 'y')
                
            selection_confirmed = ret[1]
        
        
        # Save the selected node plan.
        self.cluster['nodes'] = [
              {
                "plan" : "id:%d" % (storage_plan_id),
                "count" : plan_info[storage_plan_id]
              }
            ]
        
        
    def get_namenode_strategy(self):
        
        logger.msg("\nNow we setup the cluster's NameNodes. Select a NameNode strategy:")
        
        nn_menu = Menu('NameNode Setup', actions = [
            {'selector' : '1', 'title' : 'Single NameNode with optional Secondary NameNode\n\t' + 
                'A simple but non-highly-available, single-point-of-failure configuration.\n\t' +
                'The optional Secondary NameNode is not a standby for failover, but just a server dedicated to transaction log updation.\n\t' +
                'Suitable for small experimental clusters.\n', 
                'callback' : ClusterCreationWizard.single_nn},
                
            {'selector' : '2', 'title' : 'NameNode HA using QJM, with manual or automatic failover\n\t' +
                'A highly available configuration with one active and one standby NameNode sharing state\n\t' +
                'via a set of JournalNodes, and relying on an optional Zookeeper cluster for automatic failover\n\t' +
                'Suitable for medium production clusters.\n', 
                'callback' : ClusterCreationWizard.nn_ha_qjm},
                
            {'selector' : '3', 'title' : 'NameNode Federation\n\t' + 
                'A configuration consisting of multiple, independently configurable single or HA NameNodes\n\t' +
                'for serving different portions of the filesystem namespace, but sharing the same data nodes for storage.\n\t' + 
                'Suitable for large production clusters that cater to multiple projects with very different workloads\n',
                'callback' : ClusterCreationWizard.federated_nn},
                
            {'selector' : 'q', 'title' : 'Quit', 'callback' : ClusterCreationWizard.quit}
        ])
        
        nn_menu.select(self)

        
    def single_nn(self):
        
        # Select plan for namenode
        # Deploy secondary namenode? If yes, select plan for secondary NN
        
        nn_plan = self.select_plan('\nSelect a plan for the NameNode and type its ID: ')
        
        ret = InputUtils.get(
                ('\nDeploy a secondary NameNode? Its purpose is to take transaction log updation load off the primary NameNode.\n' +
                 'Not deploying one will probably make primary NameNode startup longer. [y/n] (default=y)'),
                ValidatorUtils.validate_yesno, None, 'y')
                
        deploy_secondary_nn = ret[1]
        secondary_nn_plan = None
        if deploy_secondary_nn:
            secondary_nn_plan = self.select_plan('\nSelect a plan for the Secondary NameNode and type its ID: ')
            
        self.cluster['namenodes'] = {
            'type' : 'single',
            'details' : {
                'primary' : nn_plan,
                'secondary' : secondary_nn_plan
            }
        }
            

        
        
    def nn_ha_qjm(self):
        # Plan for Active and Standby Namenodes
        # Plan for Journal Nodes
        # Create a new Zookeeper cluster? or select an existing one? or deploy on same nodes as journal nodes?
        nn_plan = self.select_plan('\nSelect a plan for the Active and Standby NameNodes.\n' + 
            'For filesystem with large number of small files, select a high RAM server. Now type the selected plan ID: ')
        
        min_jn_count = 3
        max_jn_count = 19 # This is an arbitrary max limit, not an official one.
        jn_count = 3
        jn_count = InputUtils.get(
            '\nSelect number of Journal nodes. There should be an ODD number of journal nodes. \n' + 
            'Since only the two NameNodes talk to Journal nodes, there need not be many of them, just enough to ensure reliability. \n' + 
            'With N JournalNodes, the cluster can function normally with upto (N - 1)/2 journal node failures. [%d-%d, default=%d] : ' 
            % (min_jn_count, max_jn_count, jn_count), 
            ValidatorUtils.validate_odd, (min_jn_count, max_jn_count), jn_count)
            
        jn_count = jn_count[1]
        
        jn_plan = self.select_plan('\nSelect a plan for the Journal nodes, and type its ID:')
        
        pass



    def federated_nn(self):
        pass

        
        
        
    def select_plan(self, prompt):
        
        table_data = [
            ['ID','Plan','Storage/node\nGB *','Cores','RAM(GB)','$/month','$/hour',
             'Free Outgoing\nTB/month']
        ]
        all_plan_ids = []
        plan_info = {}
        for p in self.plans[-1::-1]:
            plan_id = p['PLANID']
            all_plan_ids.append(str(plan_id))
            
            plan_storage_gb = p['DISK']
            
            disk_plan = self.default_disk_plans[plan_storage_gb]
            
            usable_storage = disk_plan[2] * 1024 # GB to MBs
            
            monthly_cost = p['PRICE']
            hourly_cost = p['HOURLY']
            
            storage = '{:d} ({:s})'.format(p['DISK'], '+'.join( str(d) for d in disk_plan ))
            
            cores = p['CORES']
            ram = p['RAM'] / 1024
            
            table_data.append( [
                p['PLANID'], 
                p['LABEL'], 
                storage, 
                '{:,d}'.format(cores),
                '{:,d}'.format(ram),
                '{:,.2f}'.format(monthly_cost), 
                '{:,.2f}'.format(hourly_cost),
                '{:,d}'.format(p['XFER']/1024)
            ])

        table = SingleTable(table_data)
        print(table.table)
        print('*Storage: Total storage available and its allocation (Boot + Swap + HDFS storage)')
        
        ret = InputUtils.get(prompt, ValidatorUtils.validate_set, all_plan_ids, None)
        
        selected_plan = int(ret[1])
        
        return selected_plan
        
        
        
        
        
    def quit(self):
        logger.success_msg("Goodbye!!")
        sys.exit(0)

        

class Menu(object):
    
    def __init__(self, heading, actions):
        '''
        Args:
            actions - a list of actions. Each action should have a selector string (like '1', '2', 'q', etc) which
                user should type to select the action, an action title which is displayed
                and a callback function.
        '''
        self.heading = heading
        self.actions = actions



    def select(self, wizard):
        '''
        Displays the list of actions, gets user's selection, validates the selection,
        and executes the action's callback function.
        
        Args:
           wizard - The creating wizard object that contains application specific context argument required
                by callback functions to execute their logic.
        '''
        is_valid = False
        
        while True:
            
            print()
            
            logger.success_msg(self.heading)
            logger.success_msg( '='* len(self.heading) )
            
            for action in self.actions:
                print('%s. %s' % (action['selector'], action['title']))
            
            choice = raw_input('\nChoice: ')
            for action in self.actions:
                if choice == action['selector']:
                    is_valid = True
                    action_fn = action['callback']
                    action_fn(wizard)
                    break
            
            if not is_valid:
                logger.error_msg('Invalid Choice!')
            else:
                break
            

class InputUtils(object):
    
    @staticmethod
    def get(prompt, validator, validator_args, default):
        '''
        Args:
           prompt - a prompt displayed to user to elicit input
           validator - a validating function. Any function passed here should take 2 arguments- 
                the input value and an caller defined additional arguments type,
                and return a list whose 
                first element indicates valid or not, second element is optional useful information
                to caller of this function, and  third element is a detailed error message if invalid.
           default - The default value to validate and return if user just presses enter.
           
        Return:
            The list returned by validator.
        '''
        is_valid = False
        while not is_valid:
            user_input = raw_input(prompt)
            if not user_input:
                if default is None:
                    # There is no default value, so just ask for input again
                    logger.error_msg("Please enter a value")
                    continue
                
                user_input = default
            
            ret = validator(user_input, validator_args)
            is_valid = ret[0]
            if is_valid:
                return ret
            else:
                logger.error_msg(ret[2])
        



class ValidatorUtils(object):
    
    @staticmethod
    def validate_storage_size(size, args):
        '''
        Converts a human readable disk size like "1TB" or "5 GB" to numerical 
        size in MB.
        
        Args:
            - size : a number like 100000 of a string like '48GB' or '1.2 TB'
            - args: ignored
                
        Returns:
            Tuple of ( is_valid:boolean, conv_info:dict, error) where is_valid indicates validity
            , conv_info is a dict with 'orig_input', 'size_in_mb', 'orig_input_value',
            'orig_input_unit'
            and error is an error string if is_valid is False
        '''
        conv_info = {
                'orig_input' : size,
                'orig_input_value': None,
                'orig_input_unit': None,
                'size_in_mb': None
            }
            
        ret = [False, conv_info, None]
        
        if type(size) == int:
            ret[0] = True
            conv_info['orig_input_value'] = size
            conv_info['size_in_mb']  = size
            return ret
            
        
        m = re.match('([0-9]*[\.]?[0-9]*)[\s]*([mMgGtT]{1,1}[bB])', size)
        if m:
            val = m.groups()[0]
            unit = m.groups()[1].lower()
            
            conv_info['orig_input_unit'] = unit
            
            if not val:
                ret[2] = "Invalid size. Should be '<number> MB|GB|TB': '%s'" % (size)
                return ret
                
            conv_info['orig_input_value'] = val
                
            val = float(val)
            if unit == 'mb':
                val = int(val)
                
            elif unit == 'gb':
                val = int(val * 1024)
                
            elif unit == 'tb':
                val = int(val * 1024 * 1024)
            
            conv_info['size_in_mb'] = val
            ret[0] = True
            
        else:
            ret[0] = False
            ret[2] = "Invalid size. Should be '<number> MB|GB|TB': '%s'" % (size)
            
        return ret

    @staticmethod
    def validate_int(value, args):
        '''
        Validates that value is an integer in a given range.
        
        Args:
           value - the value to validate.
           args - tuple (min_value, max_value) of valid range for value

        Returns:
            Tuple of ( is_valid:boolean, value:int, error) where is_valid indicates validity
            , value is the input value as an int,
            and error is an error string if is_valid is False
        '''
        
        min_value = args[0]
        max_value = args[1]
        
        ret = [False, value, None]
        
        try:
            value = int(value)
            ret[1] = value
            ret[0] = True
            
        except ValueError:
            ret[2] = 'Invalid value. Should be an integer in range [%d, %d]' % (min_value, max_value)
            
        else:
            if value < min_value or value > max_value:
                ret[2] = 'Invalid value. Should be an integer in range [%d, %d]' % (min_value, max_value)
        
        return ret


    @staticmethod
    def validate_odd(value, args):
        '''
        Validates that value is an *odd* integer in a given range.
        
        Args:
           value - the value to validate.
           args - tuple (min_value, max_value) of valid range for value

        Returns:
            Tuple of ( is_valid:boolean, value:int, error) where is_valid indicates validity
            , value is the input value as an int,
            and error is an error string if is_valid is False
        '''
        
        min_value = args[0]
        max_value = args[1]
        
        ret = [False, value, None]
        
        try:
            value = int(value)
            ret[1] = value
            ret[0] = True
            
        except ValueError:
            ret[2] = 'Invalid value. Should be an odd integer in range [%d, %d]' % (min_value, max_value)
            
        else:
            if value < min_value or value > max_value:
                ret[2] = 'Invalid value. Should be an odd integer in range [%d, %d]' % (min_value, max_value)
                
            elif value % 2 == 0:
                ret[2] = 'Invalid value. Should be an odd integer in range [%d, %d]' % (min_value, max_value)
                
        return ret
        

    @staticmethod
    def validate_yesno(choice, args):
        '''
        Validates a y/n input.
        
        Args:
            choice - user input
            args - ignored

        Returns:
            Tuple of ( is_valid:boolean, value:boolean, error) where is_valid indicates validity
            , value is the True if yes and False if no,
            and error is an error string if is_valid is False
        '''
        ret = [False, None, None]
        
        if choice in ['y', 'Y']:
            ret[0] = True
            ret[1] = True
            
        elif choice in ['n', 'N']:
            ret[0] = True
            ret[1] = False
        
        else:
            ret[2] = 'Invalid choice. Enter y/Y or n/N'
        
        return ret
            
        
        
    @staticmethod
    def validate_set(choice, valid_choices):
        '''
        Validates that input is one among the list of valid choices specified by 'valid_choices'.

        Args:
            choice - user input
            valid_choices - set of all valid choices

        Returns:
            Tuple of ( is_valid:boolean, value:str, error) where is_valid indicates validity
            , value is same as choice if it's found in valid_choices,
            and error is an error string if is_valid is False
        '''
        ret = [False, None, None]
        if choice in valid_choices:
            ret[0] = True
            ret[1] = choice
        else:
            ret[2] = 'Invalid choice. Enter one of %s' % (','.join(str(v) for v in valid_choices))
            
        return ret

        
class Utils(object):
    
    @staticmethod
    def mb_to_units(mbs):
        
        '''
        Given a value in MBs, converts them to higher units if it exceeds 1024 MB and returns 
        converted string.
        '''
        
        mbs = float(mbs)
        if mbs >= 1024 * 1024:
            ret_str = '%f TB' % (mbs / (1024.0 * 1024.0))
            
        elif mbs >= 1024:
            ret_str = '%f GB' % (mbs / 1024.0)
        
        else:
            ret_str = '%f MB' % (mbs)
            
        return ret_str
        
            
            
if __name__ == '__main__':

    
    wizard = MainWizard()
    wizard.start()
    
