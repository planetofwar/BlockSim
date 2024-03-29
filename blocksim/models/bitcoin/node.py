from blocksim.models.node import Node
from blocksim.models.network import Network
from blocksim.models.bitcoin.message import Message
from blocksim.models.chain import Chain
from blocksim.models.db import BaseDB
from blocksim.models.consensus import Consensus
from blocksim.models.transaction_queue import TransactionQueue
from blocksim.models.block import Block, BlockHeader
from blocksim.utils import time, get_random_values
import numpy as np

class BTCNode(Node):
    def __init__(self,
                 env,
                 network: Network,
                 location: str,
                 address: str,
                 hashrate=0,
                 is_mining=False,
                 is_selfish=False):
        # Create the Bitcoin genesis block and init the chain
        genesis = Block(BlockHeader())
        consensus = Consensus(env)
        chain = Chain(env, self, consensus, genesis, BaseDB())
        self.hashrate = hashrate
        self.is_mining = is_mining
        self.is_selfish = is_selfish
        self.have_private = False
        super().__init__(env,
                         network,
                         location,
                         address,
                         chain,
                         consensus)
        self.temp_txs = {}
        self.tx_on_transit = {}
        self.network_message = Message(self)
        if is_mining:
            # Transaction Queue to store the transactions
            self.transaction_queue = TransactionQueue(
                env, self, self.consensus)
        self._know_version = []
        self._handshaking = env.event()

    def build_new_block(self):
        """Builds a new candidate block and propagate it to the network

        We input in our model the block size limit, and also extrapolate the probability
        distribution for the number of transactions per block, based on measurements from
        the public network (https://www.blockchain.com/charts/n-transactions-per-block?timespan=2years).
        If the block size limit is 1 MB, as we know in Bitcoin, we take from the probability
        distribution the number of transactions, but if the user choose to simulate an
        environment with a 2 MB block, we multiply by two the number of transactions.
        With this we can see the performance in different block size limits."""
        if self.is_mining is False:
            raise RuntimeError(f'Node {self.location} is not a miner')
        block_size = self.env.config['bitcoin']['block_size_limit_mb']
        transactions_per_block_dist = self.env.config[
            'bitcoin']['number_transactions_per_block']
        transactions_per_block = int(
            get_random_values(transactions_per_block_dist)[0])
        pending_txs = []
        for i in range(transactions_per_block * block_size):
            if self.transaction_queue.is_empty():
                break
            pending_tx = self.transaction_queue.get()
            pending_txs.append(pending_tx)
        candidate_block = self._build_candidate_block(pending_txs, is_selfish=self.is_selfish)
        print(
            f'{self.address} at {time(self.env)}: New candidate block #{candidate_block.header.number} created {candidate_block.header.hash[:8]} with difficulty {candidate_block.header.difficulty}')
        # Add the candidate block to the chain of the miner node
        self.chain.add_block(candidate_block)
        # We need to broadcast the new candidate block across the network
        # If the node is selfish we don't broadcast the new block yet, but save it in private chain
        if not self.is_selfish:
            self.broadcast_new_blocks([candidate_block])
        else:
            self.have_private = True
            print(f'{self.address} at {time(self.env)}: Selfish miner mined in private chain: #{candidate_block.header.number} created {candidate_block.header.hash[:8]} with difficulty {candidate_block.header.difficulty}')


    def _build_candidate_block(self, pending_txs , is_selfish =False):
        # Get the current head block
        prev_block = self.chain.head
        coinbase = self.address
        timestamp = self.env.now
        difficulty = self.consensus.calc_difficulty(prev_block, timestamp)
        block_number = prev_block.header.number + 1
        #### Here I give the selfish miner gamma probabilty to win the race ###
        random_value = np.random.uniform(0,1)
        gamma = 1
        if(random_value > gamma and is_selfish):
            block_gamma = 1
        else:
            block_gamma = 0
        ######
        candidate_block_header = BlockHeader(
            prev_block.header.hash,
            block_number,
            timestamp,
            coinbase,
            difficulty,
            gamma=block_gamma)
        return Block(candidate_block_header, pending_txs)

    def _read_envelope(self, envelope):
        """It implements how bitcon P2P protocol works, more info here:
        https://bitcoin.org/en/developer-reference#p2p-network"""
        super()._read_envelope(envelope)
        if envelope.msg['id'] == 'version':
            self._receive_version(envelope)
        if envelope.msg['id'] == 'verack':
            self._receive_verack(envelope)
        if envelope.msg['id'] == 'inv':
            if envelope.msg['type'] == 'block':
                self._receive_new_inv_blocks(envelope)
            if envelope.msg['type'] == 'tx':
                self._receive_new_inv_transactions(envelope)
        if envelope.msg['id'] == 'getdata':
            if envelope.msg['type'] == 'block':
                self._send_full_blocks(envelope)
            if envelope.msg['type'] == 'tx':
                self._send_full_transactions(envelope)
        if envelope.msg['id'] == 'block':
            self._receive_full_block(envelope)
        if envelope.msg['id'] == 'tx':
            self._receive_full_transaction(envelope)

    ##              ##
    ## Handshake    ##
    ##              ##

    def connect(self, nodes: list):
        super().connect(nodes)
        for node in nodes:
            self._send_version(node.address)

    def _send_version(self, destination_address: str):
        """When a node creates an outgoing connection, it will immediately advertise its version"""
        if destination_address not in self._know_version:
            version_msg = self.network_message.version()
            print(
                f'{self.address} at {time(self.env)}: Version message sent to {destination_address}')
            self._know_version.append(destination_address)
            self.env.process(self.send(destination_address, version_msg))

    def _receive_version(self, envelope):
        """After a node receive a message it will send a ACK message, which informs the
        acceptance of the version. It also send his version to the destination, only if it
        was not send previously."""
        verack_msg = self.network_message.verack()
        print(
            f'{self.address} at {time(self.env)}: Version message received from {envelope.origin.address} and verack sent')
        self.env.process(self.send(envelope.origin.address, verack_msg))
        print(f'{self.address} at {time(self.env)}: Send the response version to {envelope.origin.address}')
        self._send_version(envelope.origin.address)

    def _receive_verack(self, envelope):
        self._handshaking.succeed()
        self._handshaking = self.env.event()
        print(
            f'{self.address} at {time(self.env)}: Receive ACK from {envelope.origin.address}')

    ##              ##
    ## Transactions ##
    ##              ##

    def request_txs(self, hashes: list, destination_address: str):
        """Request transactions to a specific node by `destination_address`"""
        for tx_hash in hashes:
            self.tx_on_transit[tx_hash] = tx_hash
        get_data_msg = self.network_message.get_data(hashes, 'tx')
        self.env.process(self.send(destination_address, get_data_msg))

    def broadcast_transactions(self, transactions: list):
        """Broadcast transactions to all nodes with an active session and mark the hashes
        as known by each node"""
        yield self.connecting  # Wait for all connections
        for node_address, node in self.active_sessions.items():
            transactions_hashes = []
            for tx in transactions:
                # Add the transaction to a temporary list
                self.temp_txs[tx.hash] = tx
                # Checks if the transaction was previous sent
                if any({tx.hash} & node.get('knownTxs')):
                    print(
                        f'{self.address} at {time(self.env)}: Transaction {tx.hash[:8]} was already sent to {node_address}')
                else:
                    self._mark_transaction(tx.hash, node_address)
                    transactions_hashes.append(tx.hash)
        # Only send if it has transactions hashes
        if transactions_hashes:
            print(
                f'{self.address} at {time(self.env)}: {len(transactions_hashes)} transaction(s) ready to be announced')
            transactions_msg = self.network_message.inv(
                transactions_hashes, 'tx')
            self.env.process(self.broadcast(transactions_msg))

    def _send_full_transactions(self, envelope):
        """Send a full transaction for any node that request it, identified by the
        `destination_address`. In `envelope.msg['hashes']` we obtain a list of hashes of
        transactions being requested
        """
        for tx_hash in envelope.msg['hashes']:
            if tx_hash in self.temp_txs:
                tx = self.temp_txs[tx_hash]
                del self.temp_txs[tx_hash]
                print(
                    f'{self.address} at {time(self.env)}: Full transaction {tx.hash[:8]} preapred to send')
                tx_msg = self.network_message.tx(tx)
                self.env.process(self.send(envelope.origin.address, tx_msg))

    def _receive_new_inv_transactions(self, envelope):
        """Handle new transactions received"""
        request_txs = []
        for tx_hash in envelope.msg.get('hashes'):
            # Only request full TX that are not on transit
            if tx_hash not in self.tx_on_transit:
                request_txs.append(tx_hash)
        # Request the full TX
        if request_txs:
            self.request_txs(request_txs, envelope.origin.address)

    def _receive_full_transaction(self, envelope):
        """Handle full tx received. If node is miner store transactions in a pool"""
        tx = envelope.msg.get('tx')
        del self.tx_on_transit[tx.hash]
        if self.is_mining:
            self.transaction_queue.put(tx)
        self.env.process(self.broadcast_transactions([tx]))

    ##              ##
    ## Blocks       ##
    ##              ##

    def broadcast_new_blocks(self, new_blocks: list):
        """Specify one or more new blocks which have appeared on the network."""
        new_blocks_hashes = [b.header.hash for b in new_blocks]
        new_blocks_msg = self.network_message.inv(new_blocks_hashes, 'block')
        self.env.process(self.broadcast(new_blocks_msg))

    def _receive_new_inv_blocks(self, envelope):
        """Handle new `inv` blocks received (https://bitcoin.org/en/developer-reference#inv).
        The destination only receives the hash of the block, and then ask for the entire block
        by calling `getdata` netowork protocol message (https://bitcoin.org/en/developer-reference#getdata)."""
        new_blocks_hashes = envelope.msg.get('hashes')
        print(f'{self.address} at {time(self.env)}: {len(new_blocks_hashes)} new blocks announced by {envelope.origin.address}')
        get_data_msg = self.network_message.get_data(new_blocks_hashes, 'block')
        self.env.process(self.send(envelope.origin.address, get_data_msg))

    def _send_full_blocks(self, envelope):
        """Send a full block (https://bitcoin.org/en/developer-reference#block) for any node that
        request it (`envelope.origin.address`) by using `getdata`.
        In `envelope.msg['hashes']` we obtain a list of hashes of full blocks being requested
        """
        origin = envelope.origin.address
        for block_hash in envelope.msg['hashes']:
            block = self.chain.get_block(block_hash)
            print(
                f'{self.address} at {time(self.env)}: Block {block.header.hash[:8]} preapred to send to {origin}')
            block_msg = self.network_message.block(block)
            self.env.process(self.send(origin, block_msg))

    def broadcast_private_chain(self,block,private_block):
        if private_block == block:
            if block != self.chain.genesis:
                print(f'{self.address} at {time(self.env)}: Selfish miner found common ancestor')
            else:
                print(f'{self.address} at {time(self.env)}: Selfish miner found common ancestor: genesiss')
            return
        self.broadcast_private_chain(self.chain.get_parent(block=block), self.chain.get_parent(block=private_block))
        self.broadcast_new_blocks([private_block])

    def _receive_full_block(self, envelope):
        """Handle full blocks received.
        The node tries to add the block to the chain, by performing validation."""
        block = envelope.msg['block']
        is_added = False
        if not self.is_selfish:
            is_added = self.chain.add_block(block)
            # If it is a selfish block, if we recived a block that is one behind the private chain - broadcast the private chain
        else:
            if (block.header.number == self.chain.head.header.number-1 or block.header.number == self.chain.head.header.number or block.header.number < self.chain.head.header.number-10):
                 print(f'{self.address} at {time(self.env)}: Selfish miner adding to secondary chain')
                 is_added = self.chain.add_block(block)
                 #Trying to race with new block
                 if block.header.number == self.chain.head.header.number and self.have_private:
                     self.broadcast_new_blocks([self.chain.head])
                     print(f'{self.address} at {time(self.env)}: Selfish miner trying to race')
                     self.have_private = False
                 #find common ancestor and broadcast the private chain
                 elif self.have_private:
                    private_block = self.chain.get_parent(block=self.chain.head)
                    if block.header.number < self.chain.head.header.number-10:
                        advantage = self.chain.head.header.number - block.header.number
                        for i in range (advantage-1):
                           private_block = self.chain.get_parent(block=private_block) 
                    self.have_private = False
                    print(f'{self.address} at {time(self.env)}: Selfish miner release private chain, heard of block number:{block.header.number}, we have:{self.chain.head.header.number}')
                    self.broadcast_private_chain(block,private_block)
                    self.broadcast_new_blocks([self.chain.head])
            else: #if the selfish miner is behind he will take the block, if he's ahed, the block will not be added in add_block function
                is_added = self.chain.add_block(block)
                print(f'{self.address} at {time(self.env)}: Selfish miner saves private chain, heard of block number:{block.header.number}, we have:{self.chain.head.header.number}')
        if is_added:
            print(
                f'{self.address} at {time(self.env)}: Block assembled and added to the tip of the chain {block.header}')
        else:
            print(
                f'{self.address} at {time(self.env)}: Block NOT added to the chain {block.header}')
