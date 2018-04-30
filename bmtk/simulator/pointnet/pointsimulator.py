# Copyright 2017. Allen Institute. All rights reserved
#
# Redistribution and use in source and binary forms, with or without modification, are permitted provided that the
# following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice, this list of conditions and the following
# disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following
# disclaimer in the documentation and/or other materials provided with the distribution.
#
# 3. Neither the name of the copyright holder nor the names of its contributors may be used to endorse or promote
# products derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES,
# INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
# WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
import os
import glob
import nest

from bmtk.simulator.core.simulator import Simulator
from bmtk.simulator.pointnet.config import Config
#import bmtk.simulator.pointnet.config as cfg
from bmtk.simulator.pointnet.io_tools import io
import bmtk.simulator.utils.simulation_reports as reports
import bmtk.simulator.utils.simulation_inputs as inputs
from bmtk.utils.io import spike_trains
import modules as mods


class PointSimulator(Simulator):
    def __init__(self, graph, dt=0.001, overwrite=True, print_time=False):
        self._duration = 0.0  # simulation time
        self._dt = dt  # time step
        self._output_dir = './output/'  # directory where log and temporary output will be stored
        self._overwrite = overwrite
        self._block_run = False
        self._block_size = -1

        self._cells_built = False
        self._internal_connections_built = False

        self._graph = graph
        self._external_cells = {}  # dict-of-dict of external pointnet cells with keys [network_name][cell_id]
        self._internal_cells = {}  # dictionary of internal pointnet cells with cell_id as key
        self._nest_id_map = {}  # a map between NEST IDs and Node-IDs

        self._spikedetector = None
        self._spikes_file = None  # File where all output spikes will be collected and saved
        self._tmp_spikes_file = None  # temporary gdf files of spike-trains
        self._spike_trains_ds = {}  # used to temporary store NWB datasets containing spike trains

        self._spike_detector = None

        self._mods = []

        # Reset the NEST kernel for a new simualtion
        # TODO: move this into it's own function and make sure it is called before network is built
        nest.ResetKernel()
        nest.SetKernelStatus({"resolution": self._dt, "overwrite_files": self._overwrite, "print_time": print_time})

    def _get_block_trial(self, duration):
        """
        Compute necessary number of block trials, the length of block simulation and the simulation length of the last
        block run, if necessary.
        """
        if self._block_run:
            data_res = self._block_size * self._dt
            fn = duration / data_res
            n = int(fn)
            res = fn - n
        else:
            n = -1
            res = -1
            data_res = -1
        return n, res, data_res

    '''
    def set_spikes_recordings(self):
        # TODO: Pass in output-dir and file name to save to
        # TODO: Allow for sorting - overwrite bionet module
        self._spike_detector = nest.Create("spike_detector", 1, {'label': os.path.join(self.output_dir, 'tmp_spike_times'),
                                             'withtime': True, 'withgid': True, 'to_file': True})
        # print self._spike_detector

        for pop_name, pop in self._graph._nestid2nodeid_map.items():
            # print pop.keys()

            nest.Connect(pop.keys(), self._spike_detector)
        # exit()
    '''

    def run(self, duration=None):
        if duration is None:
            duration = self.duration

        for mod in self._mods:
            mod.initialize(self)

        io.barrier()

        n, res, data_res = self._get_block_trial(duration)
        if n > 0:
            for r in xrange(n):
                nest.Simulate(data_res)
        if res > 0:
            nest.Simulate(res * self.dt)
        if n < 0:
            nest.Simulate(duration)

        io.barrier()
        for mod in self._mods:
            mod.finalize(self)
        io.barrier()

    def add_mod(self, mod):
        self._mods.append(mod)

    @classmethod
    def from_config(cls, configure, graph):
        # load the json file or object
        if isinstance(configure, basestring):
            config = Config.from_json(configure, validate=True)
        elif isinstance(configure, dict):
            config = configure
        else:
            raise Exception('Could not convert {} (type "{}") to json.'.format(configure, type(configure)))

        if 'run' not in config:
            raise Exception('Json file is missing "run" entry. Unable to build Bionetwork.')
        run_dict = config['run']

        # Get network parameters
        # step time (dt) is set in the kernel and should be passed
        overwrite = run_dict['overwrite_output_dir'] if 'overwrite_output_dir' in run_dict else True
        print_time = run_dict['print_time'] if 'print_time' in run_dict else False
        dt = run_dict['dt']  # TODO: make sure dt exists
        network = cls(graph, dt=dt, overwrite=overwrite)

        if 'output_dir' in config['output']:
            network.output_dir = config['output']['output_dir']

        if 'block_run' in run_dict and run_dict['block_run']:
            if 'block_size' not in run_dict:
                raise Exception('"block_run" is set to True but "block_size" not found.')
            network._block_size = run_dict['block_size']

        if 'duration' in run_dict:
            network.duration = run_dict['duration']

        # Create the output-directory, or delete existing files if it already exists
        graph.io.log_info('Setting up output directory')
        if not os.path.exists(config['output']['output_dir']):
            os.mkdir(config['output']['output_dir'])
        elif overwrite:
            for gfile in glob.glob(os.path.join(config['output']['output_dir'], '*.gdf')):
                os.remove(gfile)

        graph.io.log_info('Building cells.')
        graph.build_nodes()

        graph.io.log_info('Building recurrent connections')
        graph.build_recurrent_edges()

        for sim_input in inputs.from_config(config):
            node_set = graph.get_node_set(sim_input.node_set)
            if sim_input.input_type == 'spikes':
                spikes = spike_trains.SpikesInput(name=sim_input.name, module=sim_input.module,
                                                  input_type=sim_input.input_type, params=sim_input.params)
                io.log_info('Build virtual cell stimulations for {}'.format(sim_input.name))
                graph.add_spike_trains(spikes, node_set)

        sim_reports = reports.from_config(config)
        for report in sim_reports:
            if report.module == 'spikes_report':
                mod = mods.SpikesMod(**report.params)

            network.add_mod(mod)

        io.log_info('Network created.')
        return network