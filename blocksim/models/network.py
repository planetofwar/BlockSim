import scipy
from simpy import Store
from blocksim.utils import get_random_values, time, get_latency_delay
import ast

class Network:
    def __init__(self, env, name,window_size):
        self.env = env
        self.name = name
        self.blockchain = self.env.config['blockchain']
        self.total_hashrate = 0
        self._nodes = {}
        self._list_nodes = []
        self._list_probabilities = []
        self.window_size = window_size

    def get_node(self, address):
        return self._nodes.get(address)

    def add_node(self, node):
        self._nodes[node.address] = node
        self.total_hashrate += node.hashrate

    def _init_lists(self):
        for add, node in self._nodes.items():
            if node.is_mining:
                self._list_nodes.append(node)
                node_prob = node.hashrate / self.total_hashrate
                self._list_probabilities.append(node_prob)

    def start_heartbeat(self):
        """ The "heartbeat" frequency of any blockchain network based on PoW is time difference
        between blocks. With this function we simulate the network heartbeat frequency.

        During all the simulation, between time intervals (corresponding to the time between blocks)
        its chosen 1 or 2 nodes to broadcast a candidate block.

        We choose 2 nodes, when we want to simulate an orphan block situation.

        A fork due to orphan blocks occurs when there are two equally or nearly equally
        valid candidates for the next block of data in the blockchain.  This event can occur
        when the two blocks are found close in time, and are submitted to the network at different “ends”

        Each node has a corresponding hashrate. The greater the hashrate, the greater the
        probability of the node being chosen.
        """
        self._init_lists()
        time_last_block = -1
        daa_method = "zeno" # must be "inc" or "period" or "sliding" or "-1" or "zeno"
        window_size = self.window_size
        num_blocks = -1
        sum_time = 0
        original_mean = 560
        window = []
        while True:
            num_blocks += 1
            if time_last_block != -1 and daa_method == "inc":
                time_diff = original_mean - time_last_block
                parameters_tuple = ast.literal_eval(self.env.delays['time_between_blocks_seconds']['parameters'])
                parameters_list = list(parameters_tuple)
                parameters_list[0] = original_mean + time_diff
                self.env.delays['time_between_blocks_seconds']['parameters'] = str(tuple(parameters_list))
            elif num_blocks > 0 and num_blocks % window_size == 0 and daa_method == "period":
                time_diff = original_mean - (sum_time / window_size)
                sum_time = 0
                parameters_tuple = ast.literal_eval(self.env.delays['time_between_blocks_seconds']['parameters'])
                parameters_list = list(parameters_tuple)
                parameters_list[0] = original_mean + time_diff
                self.env.delays['time_between_blocks_seconds']['parameters'] = str(tuple(parameters_list))
            elif num_blocks > window_size and daa_method == "sliding":
                time_diff = original_mean - (sum(window) / window_size)
                parameters_tuple = ast.literal_eval(self.env.delays['time_between_blocks_seconds']['parameters'])
                parameters_list = list(parameters_tuple)
                parameters_list[0] = original_mean + time_diff
                self.env.delays['time_between_blocks_seconds']['parameters'] = str(tuple(parameters_list))
            elif num_blocks > 0 and num_blocks % window_size == 0 and daa_method == "zeno":
                time_diff = original_mean - (sum_time / window_size)
                new_time = 0.5*time_diff
                sum_time = 0
                parameters_tuple = ast.literal_eval(self.env.delays['time_between_blocks_seconds']['parameters'])
                parameters_list = list(parameters_tuple)
                parameters_list[0] = original_mean + new_time
                self.env.delays['time_between_blocks_seconds']['parameters'] = str(tuple(parameters_list))
            time_between_blocks = round(get_random_values(
                self.env.delays['time_between_blocks_seconds'])[0], 2)
            if time_between_blocks <= 0:
                time_between_blocks = 10
            window.append(time_between_blocks)
            if len(window) > window_size:
                window = window[-window_size:]
            sum_time += time_between_blocks
            time_last_block = time_between_blocks
            yield self.env.timeout(time_between_blocks)
            orphan_blocks_probability = self.env.config[self.blockchain]['orphan_blocks_probability']
            simulate_orphan_blocks = scipy.random.choice(
                [True, False], 1, p=[orphan_blocks_probability, 1-orphan_blocks_probability])[0]
            if simulate_orphan_blocks:
                selected_nodes = scipy.random.choice(
                    self._list_nodes, 2, replace=False, p=self._list_probabilities)
                for selected_node in selected_nodes:
                    self._build_new_block(selected_node)
            else:
                selected_node = scipy.random.choice(
                    self._list_nodes, 1, replace=False, p=self._list_probabilities)[0]
                self._build_new_block(selected_node)

    def _build_new_block(self, node):
        print(
            f'Network at {time(self.env)}: Node {node.address} selected to broadcast his candidate block')
        # Give orders to the selected node to broadcast his candidate block
        node.build_new_block()


class Connection:
    """This class represents the propagation through a Connection."""

    def __init__(self, env, origin_node, destination_node):
        self.env = env
        self.store = Store(env)
        self.origin_node = origin_node
        self.destination_node = destination_node

    def latency(self, envelope):
        latency_delay = get_latency_delay(
            self.env, self.origin_node.location, self.destination_node.location)
        yield self.env.timeout(latency_delay)
        self.store.put(envelope)

    def put(self, envelope):
        print(
            f'{envelope.origin.address} at {envelope.timestamp}: Message (ID: {envelope.msg["id"]}) sent with {envelope.msg["size"]} MB with a destination: {envelope.destination.address}')
        self.env.process(self.latency(envelope))

    def get(self):
        return self.store.get()
